"""
Calcoli deterministici delle metriche TikTok (codice puro — MAI AI sui numeri).

A partire dai valori TikTok estratti dal Summary di Triple Whale, calcola in USD:
- spesa, revenue attribuito, ordini, click, impression
- ROAS = revenue / spend (se non riportato direttamente)
- CPA  = spend / orders
- CVR  = orders / clicks

Valuta base = USD. Se Triple Whale riporta in EUR, spesa e revenue vengono convertiti
in USD con settings.EUR_TO_USD (riuso fx_factor di Meta) PRIMA di calcolo/salvataggio.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from src.metrics.meta import fx_factor  # riuso: USD->1, EUR->EUR_TO_USD


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
class TikTokCampaign:
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
class TikTokDaily:
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
    campaigns: list[TikTokCampaign] = field(default_factory=list)

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


def compute_tiktok_metrics(day: str, tiktok: dict) -> TikTokDaily:
    """
    Costruisce le metriche TikTok (USD) dal dict estratto da Triple Whale
    (vedi src/connectors/triplewhale.extract_tiktok).
    """
    currency = (tiktok.get("currency") or "USD").upper()
    fx = fx_factor(currency)
    daily = TikTokDaily(day=day, account_currency=currency, fx_to_usd=fx)

    daily.spend = _to_float(tiktok.get("spend")) * fx
    daily.revenue = _to_float(tiktok.get("revenue")) * fx
    daily.orders = _to_int(tiktok.get("orders"))
    daily.clicks = _to_int(tiktok.get("clicks"))
    daily.impressions = _to_int(tiktok.get("impressions"))
    reported_roas = _to_float(tiktok.get("roas"))
    daily.roas = reported_roas if reported_roas else (
        (daily.revenue / daily.spend) if daily.spend else 0.0
    )
    daily.cpa = (daily.spend / daily.orders) if daily.orders else 0.0

    for c in tiktok.get("campaigns") or []:
        spend = _to_float(c.get("spend")) * fx
        revenue = _to_float(c.get("revenue")) * fx
        orders = _to_int(c.get("orders"))
        clicks = _to_int(c.get("clicks"))
        if not (c.get("campaign_id") or spend or revenue):
            continue
        daily.campaigns.append(
            TikTokCampaign(
                day=day,
                campaign_id=str(c.get("campaign_id") or ""),
                campaign_name=c.get("campaign_name") or "(senza nome)",
                spend=spend,
                revenue=revenue,
                orders=orders,
                clicks=clicks,
                impressions=_to_int(c.get("impressions")),
                roas=(revenue / spend) if spend else 0.0,
                cpa=(spend / orders) if orders else 0.0,
                cvr=(orders / clicks) if clicks else 0.0,
            )
        )

    daily.campaigns.sort(key=lambda c: c.spend, reverse=True)
    return daily
