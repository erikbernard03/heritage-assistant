"""
Connettore Triple Whale — SOLA LETTURA, SOLO TikTok (Fase 3).

Usa l'endpoint Summary di Triple Whale per leggere le metriche giornaliere ed estrae
ESCLUSIVAMENTE il canale TikTok (spend, ROAS, impressions, clicks, conversioni e, se
disponibile, il breakdown per campagna). Meta/Shopify/Klaviyo NON vengono mai estratti
da qui: sono già collegati direttamente.

Scope API richiesti: "Summary Page: Read" + "Pixel Attribution: Read".
- UNA sola chiamata Summary al giorno (la cache su DB evita chiamate ripetute).
- Read-only: nessuna mutazione.
- Gestione rate limit (429 + Retry-After) e 5xx con backoff.

NB: lo schema esatto della risposta Summary può variare; l'estrazione TikTok è
difensiva (cerca il nodo "tiktok" e legge le metriche per alias). Il comando
/tw_check mostra la struttura reale per rifinire eventualmente il mapping.

Nessun LLM tocca questi numeri.
"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Optional

import pytz
import requests

from config import settings

# chiavi/identificatori che indicano il canale TikTok nella risposta Summary
_TIKTOK_KEYS = ("tiktok", "tiktok-ads", "tiktok_ads", "tiktokads")

# alias dei nomi metrica (Triple Whale può usare nomi diversi a seconda della vista)
_SPEND = ("spend", "adSpend", "ad_spend", "spends", "cost", "totalSpend")
_REVENUE = ("revenue", "conversionValue", "pixelConversionValue", "blendedRevenue",
            "totalRevenue", "pixelRevenue", "attributedRevenue")
_ORDERS = ("purchases", "conversions", "orders", "pixelPurchases", "totalOrders",
           "pixelConversions", "attributedPurchases")
_CLICKS = ("clicks", "totalClicks")
_IMPRESSIONS = ("impressions", "totalImpressions")
_ROAS = ("roas", "blendedRoas", "pixelRoas", "attributedRoas")


class TripleWhaleError(RuntimeError):
    """Errore generico del connettore Triple Whale."""


class TripleWhaleConnector:
    def __init__(
        self,
        api_key: Optional[str] = None,
        base: Optional[str] = None,
        summary_path: Optional[str] = None,
        shop_id: Optional[str] = None,
        timeout: int = 60,
    ):
        self.api_key = api_key or settings.TRIPLEWHALE_API_KEY
        self.base = (base or settings.TRIPLEWHALE_API_BASE).rstrip("/")
        self.summary_path = summary_path or settings.TRIPLEWHALE_SUMMARY_PATH
        # shopDomain è OBBLIGATORIO per scopare la richiesta allo shop (altrimenti 403).
        # Si legge da TRIPLEWHALE_SHOP_ID; in mancanza si usa SHOPIFY_STORE.
        self.shop_domain = (
            shop_id or settings.TRIPLEWHALE_SHOP_ID or settings.SHOPIFY_STORE
        ).strip()
        self.timeout = timeout
        if not self.api_key:
            raise TripleWhaleError("TRIPLEWHALE_API_KEY non configurata (.env).")
        self._session = requests.Session()

    def _headers(self) -> dict:
        return {
            "x-api-key": self.api_key,
            "Content-Type": "application/json",
            "accept": "application/json",
        }

    def _request(self, method: str, path: str, json_body: Optional[dict] = None) -> dict:
        url = path if path.startswith("http") else f"{self.base}{path}"
        max_retries = 5
        for attempt in range(max_retries):
            resp = self._session.request(
                method, url, headers=self._headers(), json=json_body, timeout=self.timeout
            )
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 429:
                retry_after = float(resp.headers.get("Retry-After", 2 ** attempt * 3))
                time.sleep(min(retry_after, 120))
                continue
            if resp.status_code in (500, 502, 503, 504):
                time.sleep(min(2 ** attempt * 3, 120))
                continue
            raise TripleWhaleError(f"{method} {url} -> {resp.status_code}: {resp.text[:500]}")
        raise TripleWhaleError(f"{method} {url}: esauriti i retry (rate limit?).")

    def get_me(self) -> dict:
        """Valida la API key e ritorna shop/permessi associati (GET /users/api-keys/me)."""
        return self._request("GET", "/users/api-keys/me")

    def _today_hour(self) -> int:
        """todayHour richiesto dall'API, base-1 (1–25), ora corrente Europe/Rome."""
        h = datetime.now(pytz.timezone(settings.TIMEZONE)).hour + 1
        return max(1, min(h, 25))

    def get_summary(self, start: str, end: str, today_hour: Optional[int] = None) -> dict:
        """
        Chiama l'endpoint Summary per [start, end] (YYYY-MM-DD). Raw JSON.

        Body richiesto dall'API (vedi docs Triple Whale):
            {"shopDomain": "...", "period": {"start": "...", "end": "..."}, "todayHour": N}
        shopDomain è obbligatorio: senza, l'API risponde 403 Access Denied.
        """
        if not self.shop_domain:
            raise TripleWhaleError(
                "shopDomain mancante: imposta TRIPLEWHALE_SHOP_ID (o SHOPIFY_STORE)."
            )
        body = {
            "shopDomain": self.shop_domain,
            "period": {"start": start, "end": end},
            "todayHour": today_hour if today_hour is not None else self._today_hour(),
        }
        return self._request("POST", self.summary_path, json_body=body)


# --------------------------------------------------------------------------- #
# Estrazione difensiva del SOLO canale TikTok dalla risposta Summary
# --------------------------------------------------------------------------- #
def _looks_tiktok(value) -> bool:
    return isinstance(value, str) and any(k in value.lower() for k in _TIKTOK_KEYS)


def _node_is_tiktok(node: dict) -> bool:
    """Un dict è il nodo TikTok se un suo campo identificativo contiene 'tiktok'."""
    for field in ("id", "service", "serviceId", "channel", "name", "key", "source", "provider"):
        if _looks_tiktok(node.get(field)):
            return True
    return False


def find_tiktok_node(obj):
    """
    Cerca ricorsivamente il nodo TikTok nella risposta Summary.
    Ritorna il dict del canale TikTok, oppure None.
    """
    if isinstance(obj, dict):
        if _node_is_tiktok(obj):
            return obj
        # chiave del dict che contiene 'tiktok' -> il valore è il nodo
        for k, v in obj.items():
            if _looks_tiktok(k) and isinstance(v, dict):
                return v
        for v in obj.values():
            found = find_tiktok_node(v)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = find_tiktok_node(item)
            if found is not None:
                return found
    return None


def _metric(node: dict, names) -> float:
    for n in names:
        if n in node and isinstance(node[n], (int, float, str)):
            try:
                return float(node[n])
            except (TypeError, ValueError):
                continue
    # a volte le metriche sono annidate in node["metrics"]
    metrics = node.get("metrics")
    if isinstance(metrics, dict):
        for n in names:
            if n in metrics:
                try:
                    return float(metrics[n])
                except (TypeError, ValueError):
                    continue
    return 0.0


def _extract_campaigns(node: dict) -> list[dict]:
    """Breakdown per campagna, se presente nel nodo TikTok (best-effort)."""
    out: list[dict] = []
    for key in ("campaigns", "breakdown", "children", "items", "rows"):
        val = node.get(key)
        if isinstance(val, list) and val and isinstance(val[0], dict):
            for c in val:
                out.append(
                    {
                        "campaign_id": str(
                            c.get("id") or c.get("campaignId") or c.get("campaign_id") or ""
                        ),
                        "campaign_name": c.get("name") or c.get("campaignName") or "",
                        "spend": _metric(c, _SPEND),
                        "revenue": _metric(c, _REVENUE),
                        "orders": _metric(c, _ORDERS),
                        "clicks": _metric(c, _CLICKS),
                        "impressions": _metric(c, _IMPRESSIONS),
                    }
                )
            break
    return out


def extract_tiktok(summary: dict) -> Optional[dict]:
    """
    Estrae i valori TikTok normalizzati dalla risposta Summary, o None se assente.
    I numeri sono nella valuta riportata da Triple Whale (conversione USD altrove).
    """
    node = find_tiktok_node(summary)
    if node is None:
        return None
    currency = (
        summary.get("currency")
        or summary.get("accountCurrency")
        or node.get("currency")
        or "USD"
    )
    return {
        "currency": str(currency).upper(),
        "spend": _metric(node, _SPEND),
        "revenue": _metric(node, _REVENUE),
        "orders": _metric(node, _ORDERS),
        "clicks": _metric(node, _CLICKS),
        "impressions": _metric(node, _IMPRESSIONS),
        "roas": _metric(node, _ROAS),  # se 0, verrà ricalcolato revenue/spend
        "campaigns": _extract_campaigns(node),
    }
