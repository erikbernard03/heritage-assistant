"""
Calcoli deterministici delle metriche Meta (codice puro — MAI AI sui numeri).

A partire dai record grezzi Meta (per campagna), calcola in USD:
- spesa, revenue attribuito, ordini (acquisti), click, impression
- ROAS = revenue / spend   (metriche riportate dalla piattaforma)
- CPA  = spend / orders
- CVR  = orders / clicks

Valuta base = USD. Se l'account è in EUR, spesa e revenue vengono convertiti in USD
con il tasso configurato (settings.EUR_TO_USD) PRIMA di qualsiasi calcolo/salvataggio.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from config import settings

# Priorità nel riconoscere l'azione "acquisto" (evita doppi conteggi).
_PURCHASE_TYPES = (
    "omni_purchase",
    "purchase",
    "offsite_conversion.fb_pixel_purchase",
)


def _to_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _pick_action_value(items: Optional[list], types=_PURCHASE_TYPES) -> float:
    """Estrae il primo action/action_value che corrisponde a un tipo 'acquisto'."""
    if not items:
        return 0.0
    by_type = {it.get("action_type"): _to_float(it.get("value")) for it in items}
    for t in types:
        if t in by_type:
            return by_type[t]
    return 0.0


def fx_factor(account_currency: str) -> float:
    """Fattore di conversione valuta -> USD."""
    cur = (account_currency or "USD").upper()
    if cur == "USD":
        return 1.0
    if cur == "EUR":
        return settings.EUR_TO_USD
    # valute diverse: nessun tasso configurato -> 1.0 (resta tracciato il currency)
    return 1.0


@dataclass
class MetaCampaign:
    day: str
    campaign_id: str
    campaign_name: str
    spend: float = 0.0       # USD
    revenue: float = 0.0     # USD
    orders: int = 0
    clicks: int = 0
    impressions: int = 0
    roas: float = 0.0
    cpa: float = 0.0
    cvr: float = 0.0

    def as_db_row(self) -> dict:
        return {
            "day": self.day,
            "campaign_id": self.campaign_id,
            "campaign_name": self.campaign_name,
            "spend": round(self.spend, 2),
            "revenue": round(self.revenue, 2),
            "orders": self.orders,
            "clicks": self.clicks,
            "impressions": self.impressions,
            "roas": round(self.roas, 4),
            "cpa": round(self.cpa, 2),
            "cvr": round(self.cvr, 4),
        }


@dataclass
class MetaDaily:
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
    campaigns: list[MetaCampaign] = field(default_factory=list)

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
            "account_currency": self.account_currency,
            "fx_to_usd": round(self.fx_to_usd, 6),
        }


def compute_meta_metrics(
    day: str,
    raw_rows: list[dict],
    account_currency: str = "USD",
) -> MetaDaily:
    """Aggrega i record grezzi Meta in metriche per campagna + totali account (USD)."""
    fx = fx_factor(account_currency)
    daily = MetaDaily(day=day, account_currency=(account_currency or "USD").upper(), fx_to_usd=fx)

    for row in raw_rows:
        spend = _to_float(row.get("spend")) * fx
        revenue = _pick_action_value(row.get("action_values")) * fx
        orders = int(_pick_action_value(row.get("actions")))
        clicks = int(_to_float(row.get("clicks")))
        impressions = int(_to_float(row.get("impressions")))

        camp = MetaCampaign(
            day=day,
            campaign_id=str(row.get("campaign_id") or ""),
            campaign_name=row.get("campaign_name") or "(senza nome)",
            spend=spend,
            revenue=revenue,
            orders=orders,
            clicks=clicks,
            impressions=impressions,
            roas=(revenue / spend) if spend else 0.0,
            cpa=(spend / orders) if orders else 0.0,
            cvr=(orders / clicks) if clicks else 0.0,
        )
        daily.campaigns.append(camp)

        daily.spend += spend
        daily.revenue += revenue
        daily.orders += orders
        daily.clicks += clicks
        daily.impressions += impressions

    daily.roas = (daily.revenue / daily.spend) if daily.spend else 0.0
    daily.cpa = (daily.spend / daily.orders) if daily.orders else 0.0
    # ordina le campagne per spesa decrescente (le più rilevanti prima)
    daily.campaigns.sort(key=lambda c: c.spend, reverse=True)
    return daily
