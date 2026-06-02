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

class TripleWhaleError(RuntimeError):
    """Errore generico del connettore Triple Whale."""


class TripleWhaleConnector:
    def __init__(
        self,
        api_key: Optional[str] = None,
        base: Optional[str] = None,
        summary_path: Optional[str] = None,
        shop_id: Optional[str] = None,
        timeout: int = 30,
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
        max_retries = 3
        for attempt in range(max_retries):
            resp = self._session.request(
                method, url, headers=self._headers(), json=json_body, timeout=self.timeout
            )
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 429:
                retry_after = float(resp.headers.get("Retry-After", 2 ** attempt * 3))
                time.sleep(min(retry_after, 15))
                continue
            if resp.status_code in (500, 502, 503, 504):
                time.sleep(min(2 ** attempt * 3, 15))
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
# Estrazione TikTok dai METRIC TILE del Summary.
# Il Summary è una lista di tile; ogni metrica è un tile identificato da metricId,
# e il valore del periodo è in node["values"]["current"].
# I valori TikTok sono GIÀ in USD: nessuna conversione EUR->USD.
# --------------------------------------------------------------------------- #

# Mappa: campo logico -> metricId esatto di Triple Whale (canale TikTok).
TIKTOK_METRIC_IDS = {
    "spend": "tiktok_spend",
    "roas": "tiktok_complete_payment_roas",
    "impressions": "tiktokImpressions",
    "clicks": "tiktok_clicks",
    "cpm": "averageTiktokCpm",
    "non_tracked_spend": "tiktokNonTrackedSpend",  # TikTok GMV Max Ads spend
    "orders": "tiktokPurchases",                   # conversioni/ordini (reali)
    "cpa": "tiktokCpa",                            # CPA riportato
    "revenue": "tiktokConversionValue",            # revenue attribuito (reale)
}


def _num(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def collect_metric_values(summary) -> dict:
    """Scansiona ricorsivamente tutti i tile e mappa metricId -> values.current."""
    out: dict = {}

    def walk(obj):
        if isinstance(obj, dict):
            mid = obj.get("metricId")
            vals = obj.get("values")
            if mid is not None and isinstance(vals, dict) and "current" in vals:
                out[mid] = vals["current"]
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for it in obj:
                walk(it)

    walk(summary)
    return out


def extract_tiktok(summary: dict) -> Optional[dict]:
    """
    Estrae i valori TikTok (già in USD) dai metric tile del Summary, leggendo
    values.current per ciascun metricId. Ritorna None se non c'è alcuna metrica TikTok.

    - spend totale = tiktok_spend + tiktokNonTrackedSpend (GMV Max) -> sottratto dal net profit
    - revenue = tiktokConversionValue (reale, riportato)
    - orders = tiktokPurchases · CPA = tiktokCpa (entrambi riportati)
    - nessun breakdown per campagna nel Summary (solo totali account).
    """
    vals = collect_metric_values(summary)
    if not any(mid in vals for mid in TIKTOK_METRIC_IDS.values()):
        return None

    tracked_spend = _num(vals.get(TIKTOK_METRIC_IDS["spend"]))
    non_tracked_spend = _num(vals.get(TIKTOK_METRIC_IDS["non_tracked_spend"]))
    total_spend = tracked_spend + non_tracked_spend

    return {
        "currency": "USD",                 # già USD: nessuna conversione
        "spend": total_spend,              # tiktok_spend + GMV Max (net profit)
        "tracked_spend": tracked_spend,
        "non_tracked_spend": non_tracked_spend,
        "revenue": _num(vals.get(TIKTOK_METRIC_IDS["revenue"])),  # tiktokConversionValue
        "roas": _num(vals.get(TIKTOK_METRIC_IDS["roas"])),
        "impressions": _num(vals.get(TIKTOK_METRIC_IDS["impressions"])),
        "clicks": _num(vals.get(TIKTOK_METRIC_IDS["clicks"])),
        "cpm": _num(vals.get(TIKTOK_METRIC_IDS["cpm"])),
        "orders": _num(vals.get(TIKTOK_METRIC_IDS["orders"])),    # tiktokPurchases
        "cpa": _num(vals.get(TIKTOK_METRIC_IDS["cpa"])),          # tiktokCpa (riportato)
        "campaigns": [],                   # nessun breakdown per campagna nel Summary
    }


# --------------------------------------------------------------------------- #
# Estrazione GOOGLE ADS dai metric tile del Summary (stesso pattern di TikTok).
# Valori già in USD. Solo totali account (no per-campaign: arriverà via Google Ads
# API quando il developer token sarà approvato).
# --------------------------------------------------------------------------- #
GOOGLE_METRIC_IDS = {
    "spend": "ga_adCost",
    "roas": "ga_ROAS",
    "cpa": "googleCpa",
    "clicks": "totalGoogleAdsClicks",
    "impressions": "totalGoogleAdsImpressions",
    "orders": "ga_all_transactions_adGroup",
    "revenue": "ga_all_transactionsRevenue_adGroup",
}
_GOOGLE_CPA_ALT = "googleAllCpa"

# CVR del negozio. averageGaTransactionsPerSession spesso = 0 (inutilizzabile), quindi:
# 1) preferito (più affidabile): pixelPurchases / sessions  (se esiste una metrica sessioni)
# 2) fallback: pixelConversionRate (valore già in PERCENTUALE, es. 0.4399 -> 0.44%)
_SESSIONS_METRIC_IDS = (
    "sessions", "pixelSessions", "totalSessions", "sessionsCount", "visits", "totalVisits",
)


def store_cvr_debug(summary: dict) -> dict:
    """Valori grezzi utili per capire da dove arriva la CVR (per la diagnostica)."""
    vals = collect_metric_values(summary)
    sessions_mid = next((s for s in _SESSIONS_METRIC_IDS if s in vals), None)
    return {
        "pixelPurchases": _num(vals.get("pixelPurchases")) if "pixelPurchases" in vals else None,
        "pixelConversionRate": _num(vals.get("pixelConversionRate")) if "pixelConversionRate" in vals else None,
        "sessions_metricId": sessions_mid,
        "sessions": _num(vals.get(sessions_mid)) if sessions_mid else None,
    }


def extract_store_cvr(summary: dict) -> Optional[float]:
    """
    CVR di negozio come FRAZIONE (es. 0.025 = 2.5%). None se non ricavabile.
    Priorità: pixelPurchases/sessions (se sessioni disponibili); altrimenti
    pixelConversionRate (interpretato come percentuale, >0.2 => già in %).
    """
    vals = collect_metric_values(summary)

    purchases = _num(vals.get("pixelPurchases"))
    sessions = 0.0
    for sid in _SESSIONS_METRIC_IDS:
        if sid in vals:
            s = _num(vals.get(sid))
            if s:
                sessions = s
                break
    if purchases and sessions:
        return purchases / sessions  # frazione

    if "pixelConversionRate" in vals:
        pcr = _num(vals.get("pixelConversionRate"))
        # pixelConversionRate è in PERCENTUALE (0.4399 = 0.44%). Se invece fosse una
        # frazione plausibile (<=0.2 cioè <=20%) la usiamo così com'è.
        return (pcr / 100.0) if pcr > 0.2 else pcr
    return None


def extract_google(summary: dict) -> Optional[dict]:
    """
    Estrae i valori Google Ads (già in USD) dai metric tile del Summary, leggendo
    values.current per ciascun metricId. Ritorna None se non c'è alcuna metrica Google.
    Solo totali a livello account (nessun breakdown per campagna nel Summary).
    """
    vals = collect_metric_values(summary)
    if not any(mid in vals for mid in GOOGLE_METRIC_IDS.values()):
        return None

    cpa = _num(vals.get(GOOGLE_METRIC_IDS["cpa"]))
    if not cpa:
        cpa = _num(vals.get(_GOOGLE_CPA_ALT))

    return {
        "currency": "USD",            # già USD: nessuna conversione
        "spend": _num(vals.get(GOOGLE_METRIC_IDS["spend"])),
        "revenue": _num(vals.get(GOOGLE_METRIC_IDS["revenue"])),
        "orders": _num(vals.get(GOOGLE_METRIC_IDS["orders"])),
        "clicks": _num(vals.get(GOOGLE_METRIC_IDS["clicks"])),
        "impressions": _num(vals.get(GOOGLE_METRIC_IDS["impressions"])),
        "roas": _num(vals.get(GOOGLE_METRIC_IDS["roas"])),  # se 0 -> ricalcolato
        "cpa": cpa,                                          # se 0 -> ricalcolato
    }
