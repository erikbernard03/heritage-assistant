"""
Secondo store: conversione revenue → USD (multi-valuta) e fee pagamenti per-store.
Deterministico, nessuna rete.
"""
from config import settings
from src.metrics.profit import compute_daily_metrics, order_revenue, order_revenue_usd


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
