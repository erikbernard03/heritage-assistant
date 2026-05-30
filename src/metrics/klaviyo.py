"""
Calcoli deterministici delle metriche Klaviyo (codice puro — MAI AI sui numeri).

SOLO CAMPAGNE (no flows). A partire dai risultati grezzi del Reporting API,
costruisce in USD:
- per campagna: revenue (conversion_value), opens, clicks, conversions, recipients,
  open_rate, click_rate
- totali giornalieri: somma dei campi + open_rate/click_rate ricalcolati sul totale

Valuta base = USD (conversion_value è già nella valuta dello store).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


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
class KlaviyoCampaign:
    day: str
    campaign_id: str
    campaign_name: str
    revenue: float = 0.0     # USD
    opens: int = 0
    clicks: int = 0
    conversions: int = 0
    recipients: int = 0
    open_rate: float = 0.0
    click_rate: float = 0.0

    def as_db_row(self) -> dict:
        return {
            "day": self.day,
            "campaign_id": self.campaign_id,
            "campaign_name": self.campaign_name,
            "revenue": round(self.revenue, 2),
            "opens": self.opens,
            "clicks": self.clicks,
            "conversions": self.conversions,
            "recipients": self.recipients,
            "open_rate": round(self.open_rate, 4),
            "click_rate": round(self.click_rate, 4),
        }


@dataclass
class KlaviyoDaily:
    day: str
    revenue: float = 0.0     # USD
    opens: int = 0
    clicks: int = 0
    conversions: int = 0
    recipients: int = 0
    open_rate: float = 0.0
    click_rate: float = 0.0
    campaigns: list[KlaviyoCampaign] = field(default_factory=list)

    def as_db_row(self) -> dict:
        return {
            "day": self.day,
            "revenue": round(self.revenue, 2),
            "opens": self.opens,
            "clicks": self.clicks,
            "conversions": self.conversions,
            "recipients": self.recipients,
            "open_rate": round(self.open_rate, 4),
            "click_rate": round(self.click_rate, 4),
        }


def compute_klaviyo_metrics(
    day: str,
    raw_results: list[dict],
    names: Optional[dict[str, str]] = None,
) -> KlaviyoDaily:
    """
    Aggrega i risultati grezzi del campaign-values-report in metriche per campagna
    + totali giornalieri (tutto deterministico). `names` mappa campaign_id->nome.
    """
    names = names or {}
    daily = KlaviyoDaily(day=day)

    for row in raw_results:
        groupings = row.get("groupings") or {}
        stats = row.get("statistics") or {}
        campaign_id = str(groupings.get("campaign_id") or "")
        if not campaign_id:
            continue

        revenue = _to_float(stats.get("conversion_value"))
        opens = _to_int(stats.get("opens"))
        clicks = _to_int(stats.get("clicks"))
        conversions = _to_int(stats.get("conversions"))
        recipients = _to_int(stats.get("recipients"))
        # i tassi possono arrivare dall'API; se assenti, li calcoliamo dai conteggi
        open_rate = _to_float(stats.get("open_rate")) or (
            (opens / recipients) if recipients else 0.0
        )
        click_rate = _to_float(stats.get("click_rate")) or (
            (clicks / recipients) if recipients else 0.0
        )

        camp = KlaviyoCampaign(
            day=day,
            campaign_id=campaign_id,
            campaign_name=names.get(campaign_id) or "(senza nome)",
            revenue=revenue,
            opens=opens,
            clicks=clicks,
            conversions=conversions,
            recipients=recipients,
            open_rate=open_rate,
            click_rate=click_rate,
        )
        daily.campaigns.append(camp)

        daily.revenue += revenue
        daily.opens += opens
        daily.clicks += clicks
        daily.conversions += conversions
        daily.recipients += recipients

    # tassi a livello giornaliero ricalcolati sul totale (deterministico)
    daily.open_rate = (daily.opens / daily.recipients) if daily.recipients else 0.0
    daily.click_rate = (daily.clicks / daily.recipients) if daily.recipients else 0.0
    # campagne ordinate per revenue decrescente (le più rilevanti prima)
    daily.campaigns.sort(key=lambda c: c.revenue, reverse=True)
    return daily
