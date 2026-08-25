"""
Connettore Klaviyo — SOLA LETTURA (Fase 4).

IMPORTANTE: SOLO dati a livello CAMPAGNA. I FLOWS sono esclusi per scelta — questo
modulo usa esclusivamente l'endpoint "campaign-values-report" (i flow hanno un
endpoint separato che NON viene mai chiamato).

REGOLE (coerenti col progetto):
- Private API key in sola lettura (pk_...), passata come header Authorization.
- UNA sola chiamata Reporting al giorno (il report notturno); la cache su DB
  (src/report.py) evita chiamate ripetute dai /report manuali.
- Gestione rate limit Klaviyo (429 con Retry-After) e 5xx con backoff.
- Il sistema NON modifica mai nulla su Klaviyo: solo letture (GET) e una query di
  reporting (POST a un endpoint di sola lettura, nessuna mutazione).

Nessun LLM tocca questi numeri.
"""
from __future__ import annotations

import time
from typing import Optional

import requests

from config import settings

# Statistiche richieste al Reporting API (nomi validi per le campagne).
CAMPAIGN_STATISTICS = [
    "conversion_value",  # revenue attribuito (USD)
    "opens",
    "clicks",
    "conversions",
    "recipients",
    "open_rate",
    "click_rate",
]


class KlaviyoError(RuntimeError):
    """Errore generico del connettore Klaviyo."""


class KlaviyoConnector:
    BASE = "https://a.klaviyo.com/api"

    def __init__(
        self,
        api_key: Optional[str] = None,
        revision: Optional[str] = None,
        timeout: int = 30,
    ):
        self.api_key = api_key or settings.KLAVIYO_API_KEY
        self.revision = revision or settings.KLAVIYO_API_REVISION
        self.timeout = timeout
        if not self.api_key:
            raise KlaviyoError("KLAVIYO_API_KEY non configurata (.env).")
        self._session = requests.Session()

    def _headers(self) -> dict:
        return {
            "Authorization": f"Klaviyo-API-Key {self.api_key}",
            "revision": self.revision,
            "accept": "application/json",
            "content-type": "application/json",
        }

    # --------------------------------------------------------------- requests
    def _request(self, method: str, path: str, json_body: Optional[dict] = None) -> dict:
        """Richiesta con gestione rate-limit (429 + Retry-After) e backoff su 5xx."""
        url = path if path.startswith("http") else f"{self.BASE}{path}"
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
            raise KlaviyoError(f"{method} {url} -> {resp.status_code}: {resp.text[:500]}")
        raise KlaviyoError(f"{method} {url}: esauriti i retry (rate limit?).")

    # ------------------------------------------------------------------- API
    def resolve_conversion_metric_id(self) -> str:
        """
        ID della metrica di conversione ("Placed Order"). Usa quello configurato
        in env se presente, altrimenti lo risolve dalla lista metriche.
        """
        if settings.KLAVIYO_CONVERSION_METRIC_ID:
            return settings.KLAVIYO_CONVERSION_METRIC_ID

        data = self._request("GET", "/metrics/")
        metrics = data.get("data", [])
        # preferenza: "Placed Order" (integrazione Shopify); fallback al primo utile
        for name in ("Placed Order", "Ordered Product"):
            for m in metrics:
                if (m.get("attributes") or {}).get("name") == name:
                    return m["id"]
        if metrics:
            return metrics[0]["id"]
        raise KlaviyoError("Nessuna metrica di conversione trovata su Klaviyo.")

    def _values_report(
        self, report_type: str, path: str, start_iso: str, end_iso: str,
        conversion_metric_id: str,
    ) -> list[dict]:
        """Esegue un values-report (campaign o flow) e ritorna la lista grezza dei results."""
        body = {
            "data": {
                "type": report_type,
                "attributes": {
                    "statistics": CAMPAIGN_STATISTICS,
                    "timeframe": {"start": start_iso, "end": end_iso},
                    "conversion_metric_id": conversion_metric_id,
                },
            }
        }
        data = self._request("POST", path, json_body=body)
        return ((data.get("data") or {}).get("attributes") or {}).get("results", []) or []

    def get_daily_campaign_report(
        self, start_iso: str, end_iso: str, conversion_metric_id: str
    ) -> list[dict]:
        """
        Valori per CAMPAGNA nell'intervallo [start_iso, end_iso). Endpoint
        campaign-values-report. Ogni result ha groupings (campaign_id) e statistics
        (conversion_value, opens, clicks, ...). Aggregazioni in src/metrics/klaviyo.py.
        """
        return self._values_report(
            "campaign-values-report", "/campaign-values-reports/",
            start_iso, end_iso, conversion_metric_id,
        )

    def get_flow_report(
        self, start_iso: str, end_iso: str, conversion_metric_id: str
    ) -> list[dict]:
        """
        Valori per FLOW nell'intervallo [start_iso, end_iso). Endpoint flow-values-report.
        Ogni result ha groupings (flow_id, flow_message_id) e statistics (conversion_value,
        ...). Richiede lo scope 'Flows:Read' sulla API key. Aggregazioni in metrics/klaviyo.
        """
        return self._values_report(
            "flow-values-report", "/flow-values-reports/",
            start_iso, end_iso, conversion_metric_id,
        )

    def get_flow_names(self, flow_ids: list[str]) -> dict[str, str]:
        """Mappa flow_id -> nome (GET /flows/{id}/). Errori sul singolo id non bloccano."""
        names: dict[str, str] = {}
        seen: set[str] = set()
        for fid in flow_ids:
            fid = (fid or "").strip()
            if not fid or fid in seen:
                continue
            seen.add(fid)
            try:
                data = self._request("GET", f"/flows/{fid}/")
                name = ((data.get("data") or {}).get("attributes") or {}).get("name")
                if name:
                    names[fid] = name
            except Exception as exc:  # noqa: BLE001 — i nomi sono opzionali
                print(f"[klaviyo] flow name lookup failed for {fid}: {exc}")
        return names

    def get_campaign_names(self, campaign_ids: list[str]) -> dict[str, str]:
        """
        Mappa campaign_id -> nome. I report restituiscono solo gli id; i nomi rendono
        leggibili report e risposte AI.

        Risoluzione diretta per ID (GET /campaigns/{id}/): affidabile a prescindere da
        stato (anche campagne inviate/archiviate) e paginazione. Una GET leggera per id
        unico; gli errori sul singolo id non bloccano il report.
        """
        names: dict[str, str] = {}
        seen: set[str] = set()
        for cid in campaign_ids:
            cid = (cid or "").strip()
            if not cid or cid in seen:
                continue
            seen.add(cid)
            try:
                data = self._request("GET", f"/campaigns/{cid}/")
                attrs = (data.get("data") or {}).get("attributes") or {}
                name = attrs.get("name")
                if name:
                    names[cid] = name
            except Exception as exc:  # noqa: BLE001 — i nomi sono opzionali
                print(f"[klaviyo] name lookup failed for campaign {cid}: {exc}")
        return names
