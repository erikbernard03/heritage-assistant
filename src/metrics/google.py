"""
Calcoli deterministici delle metriche Google Ads (codice puro — MAI AI sui numeri).

A partire dai valori Google estratti dal Summary di Triple Whale, costruisce in USD:
- spesa, revenue (conversion value), conversioni/ordini, click, impression
- ROAS riportato (ga_ROAS); se assente, revenue/spend
- CPA riportato (googleCpa/googleAllCpa); se assente, spend/orders

Valuta base = USD. I valori Google da Triple Whale sono già in USD: nessuna conversione.
Solo totali a livello account (nessun breakdown per campagna nel Summary).
"""
from __future__ import annotations

from dataclasses import dataclass


def _to_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _to_int(value) -> int:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return 0


@dataclass
class GoogleDaily:
    day: str
    account_currency: str = "USD"
    fx_to_usd: float = 1.0
    spend: float = 0.0       # USD
    revenue: float = 0.0     # USD
    orders: int = 0
    clicks: int = 0
    impressions: int = 0
    roas: float = 0.0
    cpa: float = 0.0
    store_cvr: float = 0.0   # CVR di negozio (frazione, es. 0.0234 = 2.34%)

    def as_db_row(self) -> dict:
        return {
            "day": self.day,
            "spend": round(self.spend, 2),
            "revenue": round(self.revenue, 2),
            "orders": self.orders,
            "clicks": self.clicks,
            "impressions": self.impressions,
            "roas": round(self.roas, 4),
            "cpa": round(self.cpa, 2),
            "store_cvr": round(self.store_cvr, 6),
            "account_currency": self.account_currency,
            "fx_to_usd": round(self.fx_to_usd, 6),
        }


def compute_google_metrics(
    day: str, google: dict, store_cvr: float = 0.0
) -> GoogleDaily:
    """Costruisce le metriche Google (USD) dal dict estratto da Triple Whale."""
    google = google or {}
    spend = _to_float(google.get("spend"))
    revenue = _to_float(google.get("revenue"))
    orders = _to_int(google.get("orders"))
    clicks = _to_int(google.get("clicks"))
    impressions = _to_int(google.get("impressions"))
    roas = _to_float(google.get("roas")) or ((revenue / spend) if spend else 0.0)
    cpa = _to_float(google.get("cpa")) or ((spend / orders) if orders else 0.0)
    return GoogleDaily(
        day=day,
        account_currency="USD",
        fx_to_usd=1.0,
        spend=spend,
        revenue=revenue,
        orders=orders,
        clicks=clicks,
        impressions=impressions,
        roas=roas,
        cpa=cpa,
        store_cvr=_to_float(store_cvr),
    )
