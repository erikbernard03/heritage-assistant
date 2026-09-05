"""
Vendite per PAESE (sales by location) dai line/ordini Shopify. Codice puro/deterministico.

Il paese è quello di SPEDIZIONE dell'ordine (shipping_address.country_code), con fallback
all'indirizzo di fatturazione e infine 'Unknown'. Revenue = total_price (come nel report).
Gli ordini cancellati sono esclusi.
"""
from __future__ import annotations

from src.metrics.profit import order_revenue


def _to_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def country_of(order: dict) -> str:
    """Paese dell'ordine: shipping_address, poi billing_address, infine 'Unknown'."""
    for key in ("shipping_address", "billing_address"):
        addr = order.get(key) or {}
        code = addr.get("country_code") or addr.get("country")
        if code:
            return str(code).strip().upper() if len(str(code)) == 2 else str(code).strip()
    return "Unknown"


def revenue_by_country(orders: list[dict]) -> dict[str, dict]:
    """
    {country: {revenue, orders}} dagli ordini del giorno (cancellati esclusi).
    Revenue in USD (total_price, come la revenue del report).
    """
    out: dict[str, dict] = {}
    for o in orders:
        if o.get("cancelled_at"):
            continue
        c = country_of(o)
        acc = out.setdefault(c, {"revenue": 0.0, "orders": 0})
        acc["revenue"] += order_revenue(o)
        acc["orders"] += 1
    return out


def sales_by_country_by_month(rows: list[dict]) -> dict[str, dict[str, dict]]:
    """
    Aggrega le righe sales_by_country_daily per mese: {mese: {country: {revenue, orders}}}.
    `rows`: righe con day, country, revenue, orders.
    """
    by: dict[str, dict[str, dict]] = {}
    for r in rows:
        month = str(r.get("day", ""))[:7]
        country = r.get("country") or "Unknown"
        acc = by.setdefault(month, {}).setdefault(country, {"revenue": 0.0, "orders": 0})
        acc["revenue"] += _to_float(r.get("revenue"))
        try:
            acc["orders"] += int(r.get("orders") or 0)
        except (TypeError, ValueError):
            pass
    return {k: by[k] for k in sorted(by)}
