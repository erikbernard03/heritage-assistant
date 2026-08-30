"""
Aggregazioni temporali delle vendite (per ORA del giorno) dagli ordini Shopify.
Il fuso è Europe/Rome: l'ora si ricava da created_at dell'ordine convertito a Roma.
Codice puro/deterministico.
"""
from __future__ import annotations

from datetime import datetime

import pytz

from config import settings


def _to_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _parse_iso(ts: str) -> datetime | None:
    """Parsa un timestamp ISO 8601 (con offset o 'Z') in datetime tz-aware."""
    if not ts:
        return None
    s = str(ts).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = pytz.utc.localize(dt)
    return dt


def revenue_by_hour(orders: list[dict], tz_name: str | None = None) -> dict[int, dict]:
    """
    {ora(0–23): {revenue, orders}} dagli ordini del giorno, per ORA di Europe/Rome
    (created_at convertito). Cancellati esclusi. Revenue = total_price (USD).
    """
    tz = pytz.timezone(tz_name or settings.TIMEZONE)
    out: dict[int, dict] = {}
    for o in orders:
        if o.get("cancelled_at"):
            continue
        dt = _parse_iso(o.get("created_at"))
        if dt is None:
            continue
        hour = dt.astimezone(tz).hour
        acc = out.setdefault(hour, {"revenue": 0.0, "orders": 0})
        acc["revenue"] += _to_float(o.get("total_price"))
        acc["orders"] += 1
    return out


def sales_by_hour_by_month(rows: list[dict]) -> dict[str, dict[int, dict]]:
    """
    Aggrega le righe sales_by_hour_daily per mese: {mese: {ora: {revenue, orders}}}.
    `rows`: righe con day, hour, revenue, orders.
    """
    by: dict[str, dict[int, dict]] = {}
    for r in rows:
        month = str(r.get("day", ""))[:7]
        try:
            hour = int(r.get("hour"))
        except (TypeError, ValueError):
            continue
        acc = by.setdefault(month, {}).setdefault(hour, {"revenue": 0.0, "orders": 0})
        acc["revenue"] += _to_float(r.get("revenue"))
        try:
            acc["orders"] += int(r.get("orders") or 0)
        except (TypeError, ValueError):
            pass
    return {k: by[k] for k in sorted(by)}
