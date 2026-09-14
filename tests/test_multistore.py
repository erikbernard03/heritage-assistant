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
