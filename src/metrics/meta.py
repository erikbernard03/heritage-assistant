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
from datetime import datetime
from typing import Optional

import pytz

from config import settings

# Nome del campo di breakdown orario di Meta (ore nel fuso dell'inserzionista/account).
HOURLY_BREAKDOWN = "hourly_stats_aggregated_by_advertiser_time_zone"

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


def _hour_of(hourly_bucket: str) -> int:
    """Estrae l'ora (0–23) dal valore del breakdown (es. '23:00:00 - 23:59:59' -> 23)."""
    try:
        return int(str(hourly_bucket).strip().split(":")[0])
    except (ValueError, IndexError, AttributeError):
        return 0


def rome_day_for_hour(
    date_start: str,
    hourly_bucket: str,
    account_tz_name: str,
    target_tz_name: str = None,
) -> str:
    """
    Converte (giorno nel fuso account, ora nel fuso account) nel GIORNO di calendario
    Europe/Rome corrispondente (ISO 'YYYY-MM-DD').

    Meta riporta il breakdown orario nel fuso DELL'ACCOUNT (es. Asia/Dubai): questa
    funzione prende quell'istante e lo riproietta sul fuso dei report (Europe/Rome),
    gestendo correttamente offset e ora legale via pytz.
    Esempio: Dubai (UTC+4) 2026-08-12 01:00 -> Rome (CEST, UTC+2) 2026-08-11 23:00
    -> giorno di Roma '2026-08-11'.
    """
    target_tz_name = target_tz_name or settings.TIMEZONE
    acct_tz = pytz.timezone(account_tz_name)
    rome_tz = pytz.timezone(target_tz_name)
    hour = _hour_of(hourly_bucket)
    naive = datetime.strptime(date_start, "%Y-%m-%d").replace(hour=hour)
    localized = acct_tz.localize(naive)          # istante reale nel fuso account
    return localized.astimezone(rome_tz).date().isoformat()


def rebucket_hourly_to_daily_rows(
    hourly_rows: list[dict],
    account_tz_name: str,
    target_tz_name: str = None,
) -> dict[str, list[dict]]:
    """
    Ri-bucketizza le righe orarie Meta (campagna × ora nel fuso account) nei GIORNI di
    Europe/Rome. Ritorna: { giorno_roma -> [riga giornaliera per campagna, ...] }.

    Ogni riga di output ha la STESSA forma dei record giornalieri grezzi Meta
    (campaign_id, campaign_name, spend, impressions, clicks, actions, action_values),
    così può essere passata a compute_meta_metrics SENZA modifiche: spesa/valuta,
    estrazione acquisti, ROAS/CPA restano calcolati in un unico posto.

    IMPORTANTE: i numeri così ottenuti differiranno LEGGERMENTE dalla vista giornaliera
    di Ads Manager (che usa i giorni del fuso account, es. Dubai): è voluto — qui i
    giorni sono allineati a Shopify/Europe/Rome (mezzanotte-mezzanotte di Roma).
    """
    # accumulo per (giorno_roma, campaign_id) in VALUTA ACCOUNT (la conversione fx
    # avverrà una sola volta in compute_meta_metrics).
    acc: dict[tuple, dict] = {}
    for r in hourly_rows:
        date_start = r.get("date_start")
        if not date_start:
            continue
        rome_day = rome_day_for_hour(
            date_start, r.get(HOURLY_BREAKDOWN, "00:00:00"), account_tz_name, target_tz_name
        )
        cid = str(r.get("campaign_id") or "")
        key = (rome_day, cid)
        a = acc.setdefault(
            key,
            {
                "campaign_name": r.get("campaign_name") or "(senza nome)",
                "spend": 0.0, "impressions": 0, "clicks": 0,
                "orders": 0.0, "revenue": 0.0,
            },
        )
        a["spend"] += _to_float(r.get("spend"))
        a["impressions"] += int(_to_float(r.get("impressions")))
        a["clicks"] += int(_to_float(r.get("clicks")))
        a["orders"] += _pick_action_value(r.get("actions"))
        a["revenue"] += _pick_action_value(r.get("action_values"))

    out: dict[str, list[dict]] = {}
    for (rome_day, cid), a in acc.items():
        # Ri-emette acquisti/revenue come azioni 'purchase' sintetiche: compute_meta_metrics
        # le riconosce (sono in _PURCHASE_TYPES) e applica fx + ROAS/CPA come sempre.
        out.setdefault(rome_day, []).append({
            "campaign_id": cid,
            "campaign_name": a["campaign_name"],
            "spend": a["spend"],
            "impressions": a["impressions"],
            "clicks": a["clicks"],
            "actions": [{"action_type": "purchase", "value": a["orders"]}],
            "action_values": [{"action_type": "purchase", "value": a["revenue"]}],
        })
    return out


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
