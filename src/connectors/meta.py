"""
Connettore Meta Marketing API — SOLA LETTURA (Fase 2).

REGOLE ANTI-BAN (vincolanti, da CLAUDE.md):
- Solo permessi di lettura: `ads_read` e `read_insights`. MAI `ads_management`.
- System User token via Business Manager (passato come access token).
- UNA sola pull di insights al giorno (il report notturno). Niente loop/polling.
  La cache su database (vedi src/report.py) evita chiamate ripetute dai /report manuali.
- Rispetta i rate limit: legge gli header di usage (X-Ad-Account-Usage,
  X-Business-Use-Case-Usage), gestisce l'errore 613 e usa backoff.
- Il sistema NON agisce mai sull'account: solo GET di insights.

Nessun LLM tocca questi numeri.
"""
from __future__ import annotations

import json
import time
from typing import Optional

import requests

from config import settings

# Codici errore Meta che indicano rate limit / throttling -> backoff e retry.
_RATE_LIMIT_CODES = {4, 17, 32, 613, 80000, 80004}


class MetaError(RuntimeError):
    """Errore generico del connettore Meta."""


class MetaConnector:
    def __init__(
        self,
        access_token: Optional[str] = None,
        ad_account_id: Optional[str] = None,
        api_version: Optional[str] = None,
        timeout: int = 30,
    ):
        self.access_token = access_token or settings.META_ACCESS_TOKEN
        self.api_version = api_version or settings.META_API_VERSION
        self.timeout = timeout
        raw_id = (ad_account_id or settings.META_AD_ACCOUNT_ID).strip()
        # normalizza: l'API vuole il prefisso "act_"
        self.ad_account_id = raw_id if raw_id.startswith("act_") else f"act_{raw_id}"

        if not self.access_token:
            raise MetaError("META_ACCESS_TOKEN non configurato (.env).")
        if not raw_id:
            raise MetaError("META_AD_ACCOUNT_ID non configurato (.env).")

        self._session = requests.Session()

    @property
    def _base(self) -> str:
        return f"https://graph.facebook.com/{self.api_version}"

    # --------------------------------------------------------------- requests
    def _get(self, path: str, params: dict) -> dict:
        """GET singolo con gestione rate-limit (header usage + codici 613/4/17) e backoff."""
        url = path if path.startswith("http") else f"{self._base}/{path}"
        params = {**params, "access_token": self.access_token}
        max_retries = 3
        for attempt in range(max_retries):
            resp = self._session.get(url, params=params, timeout=self.timeout)

            # Log soft degli header di usage (utile per non sforare i limiti)
            self._last_usage = {
                "ad_account": resp.headers.get("X-Ad-Account-Usage"),
                "business_use_case": resp.headers.get("X-Business-Use-Case-Usage"),
                "app_usage": resp.headers.get("X-App-Usage"),
            }

            if resp.status_code == 200:
                return resp.json()

            # prova a estrarre il codice errore Meta
            code = None
            try:
                code = (resp.json().get("error") or {}).get("code")
            except Exception:  # noqa: BLE001
                pass

            if code in _RATE_LIMIT_CODES or resp.status_code in (429, 500, 502, 503):
                sleep_s = min(2 ** attempt * 3, 15)  # backoff bounded: 3,6,12
                time.sleep(sleep_s)
                continue

            raise MetaError(
                f"GET {url} -> {resp.status_code}: {resp.text[:500]}"
            )
        raise MetaError(f"GET {url}: esauriti i retry (rate limit?).")

    # Nome del breakdown orario "nel fuso dell'inserzionista" (l'ad account).
    HOURLY_BREAKDOWN = "hourly_stats_aggregated_by_advertiser_time_zone"

    _INSIGHT_FIELDS = (
        "campaign_id",
        "campaign_name",
        "spend",
        "impressions",
        "clicks",
        "actions",
        "action_values",
    )

    # ------------------------------------------------------------------- API
    def get_account_currency(self) -> str:
        """Valuta dell'ad account (per convertire in USD). Chiamata leggera di metadati."""
        data = self._get(self.ad_account_id, {"fields": "currency"})
        return (data.get("currency") or "USD").upper()

    def get_account_timezone_name(self) -> str:
        """
        Fuso orario dell'ad account (es. 'Asia/Dubai'), letto DINAMICAMENTE dai metadati
        (mai hardcodato). È il fuso in cui Meta bucketizza i giorni e il breakdown orario
        `hourly_stats_aggregated_by_advertiser_time_zone`. Fallback 'UTC' se assente.
        """
        data = self._get(self.ad_account_id, {"fields": "timezone_name"})
        return data.get("timezone_name") or "UTC"

    def _paged_insights(self, params: dict) -> list[dict]:
        """Esegue la GET insights seguendo la paginazione e ritorna tutte le righe."""
        rows: list[dict] = []
        data = self._get(f"{self.ad_account_id}/insights", params)
        while True:
            rows.extend(data.get("data", []))
            next_url = (data.get("paging") or {}).get("next")
            if not next_url:
                break
            data = self._get(next_url, {})
        return rows

    def get_campaign_insights(
        self, since: str, until: str, time_increment: Optional[int] = 1
    ) -> list[dict]:
        """
        Pull insights per CAMPAGNA nell'intervallo [since, until] (YYYY-MM-DD).

        time_increment=1 -> una riga per campagna PER GIORNO (ogni riga ha date_start).
        time_increment=None -> aggregato per campagna sull'intero intervallo.
        I giorni sono nel FUSO DELL'ACCOUNT (es. Dubai): usa get_hourly_campaign_insights
        per ri-bucketizzare nei giorni di Roma. Ritorna i record grezzi Meta.
        """
        params = {
            "level": "campaign",
            "time_range": json.dumps({"since": since, "until": until}),
            "fields": ",".join(self._INSIGHT_FIELDS),
            "limit": 500,
        }
        if time_increment:
            params["time_increment"] = time_increment
        return self._paged_insights(params)

    def get_hourly_campaign_insights(self, since: str, until: str) -> list[dict]:
        """
        Pull insights per CAMPAGNA × ORA nell'intervallo [since, until] (giorni nel fuso
        DELL'ACCOUNT), con breakdown `hourly_stats_aggregated_by_advertiser_time_zone`.

        Ogni riga ha: date_start (giorno nel fuso account), il campo del breakdown orario
        (es. '23:00:00 - 23:59:59'), più campaign_id/name, spend, impressions, clicks,
        actions, action_values. Serve a ri-bucketizzare le ore nei giorni di Europe/Rome
        (vedi src/metrics/meta.py: rebucket_hourly_to_daily_rows).
        """
        params = {
            "level": "campaign",
            "time_range": json.dumps({"since": since, "until": until}),
            "time_increment": 1,
            "breakdowns": self.HOURLY_BREAKDOWN,
            "fields": ",".join(self._INSIGHT_FIELDS),
            "limit": 500,
        }
        return self._paged_insights(params)

    def get_daily_campaign_insights(self, day: str) -> list[dict]:
        """
        UNICA pull insights del giorno: una riga per campagna per il giorno `day`
        (YYYY-MM-DD, nel FUSO DELL'ACCOUNT). Ritorna i record grezzi Meta (la conversione
        valuta e i calcoli avvengono in src/metrics/meta.py, deterministicamente).
        NB: questo è il percorso di FALLBACK; il default ora è il re-bucketing orario→Roma.
        """
        return self.get_campaign_insights(day, day, time_increment=1)
