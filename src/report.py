"""
Orchestratore del report giornaliero (Fase 1, solo Shopify).

Flusso:
  1. calcola l'intervallo "ieri" in Europe/Rome
  2. tira gli ordini da Shopify (client credentials grant)
  3. costruisce la mappa product_id -> handle (per il COGS)
  4. calcola le metriche deterministiche (net profit, AOV, ...)
  5. (opzionale) salva ordini/line items/metriche su Supabase
  6. formatta il messaggio Telegram

Nessun LLM tocca i numeri.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Optional

import pytz

from config import settings
from src.connectors.shopify import ShopifyConnector
from src.metrics.profit import DailyMetrics, compute_daily_metrics


@dataclass
class DayWindow:
    day_str: str            # YYYY-MM-DD (Europe/Rome)
    start: datetime         # inizio giorno, tz-aware
    end: datetime           # fine giorno (esclusiva), tz-aware


def yesterday_window(now: Optional[datetime] = None) -> DayWindow:
    """Intervallo [00:00, 24:00) di IERI in Europe/Rome, tz-aware."""
    tz = pytz.timezone(settings.TIMEZONE)
    now = now.astimezone(tz) if now else datetime.now(tz)
    yesterday = (now - timedelta(days=1)).date()
    start = tz.localize(datetime.combine(yesterday, time.min))
    end = start + timedelta(days=1)
    return DayWindow(day_str=yesterday.isoformat(), start=start, end=end)


def build_daily_report(
    window: Optional[DayWindow] = None,
    persist: bool = True,
) -> tuple[DailyMetrics, str]:
    """Costruisce le metriche + il testo del report per la finestra indicata (default: ieri)."""
    window = window or yesterday_window()

    shop = ShopifyConnector()
    orders = shop.get_orders(window.start, window.end)
    handle_map = shop.get_products_handle_map()

    # annota il giorno Europe/Rome su ogni ordine (per la persistenza)
    for o in orders:
        o["_day_rome"] = window.day_str

    metrics = compute_daily_metrics(
        day=window.day_str,
        orders=orders,
        handle_map=handle_map,
        ads_spend=0.0,  # Fase 1: nessuna piattaforma ads collegata
    )

    if persist:
        _persist(orders, handle_map, metrics)

    return metrics, format_report(metrics)


def _persist(orders: list[dict], handle_map: dict[int, str], metrics: DailyMetrics) -> None:
    """Salva su Supabase se configurato; non blocca il report in caso di assenza DB."""
    try:
        from src.db.supabase_client import SupabaseStore

        store = SupabaseStore()
        store.upsert_orders(orders, handle_map)
        store.upsert_line_items(metrics)
        store.upsert_daily_metrics(metrics)
    except Exception as exc:  # il report deve arrivare comunque
        print(f"[report] persistenza Supabase saltata: {exc}")


def format_report(m: DailyMetrics) -> str:
    """Formatta il report Telegram (Markdown). Tutti i valori in USD."""
    fixed_line = ""
    if settings.INCLUDI_COSTI_FISSI_IN_NET_PROFIT:
        fixed_line = (
            f"   • Fixed-costs allocation: −${m.fixed_cost_daily:,.2f}\n"
        )
    return (
        f"📊 *Shopify report — {m.day}*\n"
        f"_(currency: USD)_\n\n"
        f"🛒 Orders: *{m.num_orders}*\n"
        f"💰 Revenue: *${m.revenue:,.2f}*\n"
        f"🧾 AOV: ${m.aov:,.2f}\n\n"
        f"*Costs for the day*\n"
        f"   • Product COGS: −${m.cogs_total:,.2f}\n"
        f"   • Shipping ($7 × {m.num_orders}): −${m.shipping_total:,.2f}\n"
        f"   • Payment fees (7.5%): −${m.payment_fees:,.2f}\n"
        f"{fixed_line}\n"
        f"*Net profit*\n"
        f"   • Operating (excl. fixed costs): *${m.net_profit_operativo:,.2f}*\n"
        f"   • Net (incl. fixed costs): *${m.net_profit_netto:,.2f}*\n"
    )


def build_monthly_pl(year: int, month: int) -> str:
    """
    P&L mensile DETERMINISTICO (codice puro, nessuna AI): aggrega le righe di
    daily_metrics del mese richiesto. Tutti i valori in USD.
    """
    from calendar import monthrange

    from src.db.supabase_client import SupabaseStore

    last_day = monthrange(year, month)[1]
    start = f"{year:04d}-{month:02d}-01"
    end = f"{year:04d}-{month:02d}-{last_day:02d}"

    store = SupabaseStore()
    rows = store.get_daily_metrics_range(start, end)
    if not rows:
        return f"📒 *P&L {year}-{month:02d}* — no data available for this month."

    def _s(key: str) -> float:
        return sum(float(r.get(key) or 0) for r in rows)

    revenue = _s("revenue")
    num_orders = int(_s("num_orders"))
    cogs = _s("cogs_total")
    shipping = _s("shipping_total")
    fees = _s("payment_fees")
    ads = _s("ads_spend")
    fixed = _s("fixed_cost_daily")
    op = _s("net_profit_operativo")
    net = _s("net_profit_netto")
    aov = (revenue / num_orders) if num_orders else 0.0

    return (
        f"📒 *P&L {year}-{month:02d}* _(USD, {len(rows)} days with data)_\n\n"
        f"🛒 Orders: *{num_orders}*\n"
        f"💰 Revenue: *${revenue:,.2f}*\n"
        f"🧾 AOV: ${aov:,.2f}\n\n"
        f"*Costs*\n"
        f"   • COGS: −${cogs:,.2f}\n"
        f"   • Shipping: −${shipping:,.2f}\n"
        f"   • Payment fees: −${fees:,.2f}\n"
        f"   • Ad spend: −${ads:,.2f}\n"
        f"   • Fixed costs: −${fixed:,.2f}\n\n"
        f"*Net profit for the month*\n"
        f"   • Operating: *${op:,.2f}*\n"
        f"   • Net: *${net:,.2f}*\n"
    )


if __name__ == "__main__":
    # Stampa a video il report di ieri (Shopify reale), senza Telegram.
    _, _text = build_daily_report(persist=False)
    print(_text)
