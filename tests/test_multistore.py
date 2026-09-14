"""
Secondo store: conversione revenue → USD (multi-valuta) e fee pagamenti per-store.
Deterministico, nessuna rete.
"""
from config import settings
from src.metrics.fixed_costs import daily_fixed_allocation
from src.metrics.profit import (
    combine_daily_metrics,
    compute_daily_metrics,
    order_revenue,
    order_revenue_usd,
)


def test_order_revenue_usd_uses_shop_money_and_fx():
    # shop_money nella valuta base dello store (EUR); presentment diverso; fx EUR->USD 1.08.
    o = {
        "current_total_price_set": {
            "shop_money": {"amount": "100.00", "currency_code": "EUR"},
            "presentment_money": {"amount": "100.00", "currency_code": "EUR"},
        },
        "current_total_price": "100.00", "total_price": "100.00",
    }
    assert round(order_revenue_usd(o, 1.08), 2) == 108.00
    # store USD (fx 1.0): shop_money è già USD
    o_usd = {"current_total_price_set": {"shop_money": {"amount": "131.00"}}}
    assert round(order_revenue_usd(o_usd, 1.0), 2) == 131.00


def test_order_revenue_usd_fallback_when_no_set():
    # nessun *_set: fallback a current_total_price (come order_revenue), poi ×fx
    o = {"current_total_price": "84.00", "total_price": "100.00"}
    assert order_revenue_usd(o, 1.0) == order_revenue(o) == 84.0
    assert round(order_revenue_usd(o, 1.08), 2) == 90.72


def test_compute_daily_metrics_per_store_fee_and_currency():
    orders = [{"id": 1, "current_total_price_set": {"shop_money": {"amount": "200.00"}},
               "line_items": []}]
    m = compute_daily_metrics("2026-09-11", orders, {}, fee_rate=0.05, currency_to_usd=1.08)
    assert round(m.revenue, 2) == 216.00               # 200 × 1.08
    assert round(m.payment_fees, 2) == 10.80           # 5% del .co
    assert round(m.shipping_total, 2) == 7.00          # $7/ordine, currency-agnostico


def test_com_defaults_unchanged():
    # Default (nessun fee_rate/fx) = comportamento .com invariato (7.5%, USD).
    orders = [{"id": 1, "current_total_price": "100.00", "line_items": []}]
    m = compute_daily_metrics("2026-09-11", orders, {})
    assert round(m.revenue, 2) == 100.00
    assert round(m.payment_fees, 2) == 7.50


def test_per_store_fee_config():
    assert settings.PAYMENT_FEE_RATE_BY_STORE["com"] == 0.075
    assert settings.PAYMENT_FEE_RATE_BY_STORE["co"] == 0.05


def test_breakeven_fee_rate_per_store():
    from src.metrics.profit import compute_breakeven_full

    rows = [{"revenue": 1000.0, "num_orders": 10, "cogs_total": 200.0}]  # AOV 100, COGS/ord 20
    com = compute_breakeven_full(rows, fee_rate=0.075)   # 100-20-7.5-7 = 65.5
    co = compute_breakeven_full(rows, fee_rate=0.05)     # 100-20-5-7   = 68.0
    assert round(com["cpa"], 2) == 65.50
    assert round(co["cpa"], 2) == 68.00                  # .co ha più margine (fee più bassa)
    assert co["cpa"] > com["cpa"] and co["roas"] < com["roas"]


def test_store_view_and_section_rendering():
    from src.metrics.store_report import (
        build_store_view,
        format_store_section,
        format_total_line,
        store_breakeven,
    )

    be = store_breakeven([{"revenue": 1000.0, "num_orders": 10, "cogs_total": 200.0}], "com")
    v = build_store_view("com", revenue=1000.0, orders=10, cogs_total=200.0, ads_spend=100.0,
                         breakeven=be)
    assert round(v["aov"], 2) == 100.0
    assert round(v["payment_fees"], 2) == 75.0           # 7.5% × 1000
    assert round(v["shipping_total"], 2) == 70.0         # $7 × 10
    assert round(v["net_operating"], 2) == round(1000 - 200 - 70 - 75 - 100, 2)  # 555
    sec = format_store_section(v, meta_daily={"roas": 3.0}, google_daily=None)
    assert "🏪 *heritagering.com* _(USD)_" in sec
    assert "Fees $75.00 (7.5%)" in sec
    assert "📣 Ad spend $100.00" in sec
    assert "_(own AOV/COGS)_" in sec
    assert "📣 Meta ROAS 3.00x (break-even" in sec       # vs store break-even

    # .co con 0 ordini -> "no orders"
    v0 = build_store_view("co", revenue=0.0, orders=0, cogs_total=0.0, ads_spend=0.0, breakeven={})
    assert format_store_section(v0) == "\n🌎 *heritagering.co* — no orders"

    # TOTAL: net = operating − fixed (pot condiviso a livello totale)
    total = format_total_line(revenue=1500.0, orders=15, net_operating=700.0, fixed_cost=404.53)
    assert "Σ *TOTAL*" in total and "*$295.47*" in total  # 700 − 404.53


def test_combine_daily_metrics_sums_and_fixed_once():
    day = "2026-09-11"
    m_com = compute_daily_metrics(day, [{"id": 1, "current_total_price": "100.00",
                                         "line_items": []}], {}, fee_rate=0.075)
    m_com.store_sessions = 300
    m_co = compute_daily_metrics(day, [{"id": 2, "current_total_price": "100.00",
                                        "line_items": []}], {}, fee_rate=0.05,
                                 currency_to_usd=1.0)
    m_co.store_sessions = 40

    c = combine_daily_metrics(day, [m_com, m_co])
    assert round(c.revenue, 2) == 200.00
    assert c.num_orders == 2
    assert round(c.shipping_total, 2) == 14.00          # $7 × 2 (entrambi gli store)
    assert round(c.payment_fees, 2) == 12.50            # 7.5 (.com) + 5 (.co) — aliquote per-store
    # Costi fissi UNA sola volta (pot condiviso), non la somma delle due parti.
    fixed = daily_fixed_allocation(day)
    assert round(c.fixed_cost_daily, 2) == round(fixed, 2)
    assert round(c.net_profit_operativo, 2) == round(200 - 14 - 12.5, 2)   # 173.50
    assert round(c.net_profit_netto, 2) == round(c.net_profit_operativo - fixed, 2)
    assert c.store_sessions == 340                       # sessioni sommate tra store
    assert round(c.aov, 2) == 100.00
