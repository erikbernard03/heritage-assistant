"""
Test deterministici del calcolo net profit (nessuna rete, nessuna credenziale).

Verificano che la matematica torni esattamente, secondo la formula della spec.
"""
from src.config_loader import CogsResolver
from src.metrics.profit import compute_daily_metrics

RESOLVER = CogsResolver()  # usa config/cogs.yaml reale


def _order(order_id, total, line_items, cancelled=False):
    return {
        "id": order_id,
        "total_price": total,
        "cancelled_at": "2026-05-29T10:00:00Z" if cancelled else None,
        "line_items": line_items,
    }


def test_cogs_resolution():
    # handle custom noto
    assert RESOLVER.cogs_for_handle("personalized-sterling-silver-signet-ring") == 76.54
    # classic ring -> 3
    assert RESOLVER.cogs_for_handle("carnelian-signet-ring") == 3
    # sconosciuto -> default 3
    assert RESOLVER.cogs_for_handle("prodotto-inesistente-xyz") == 3
    # match per titolo quando l'handle non è noto
    assert RESOLVER.cogs_for_handle(None, "Carnelian Signet Ring") == 3


def test_daily_metrics_basic():
    # 2 ordini: uno personalized sterling (76.54) + uno classic (3)
    handle_map = {111: "personalized-sterling-silver-signet-ring", 222: "carnelian-signet-ring"}
    orders = [
        _order(1, "100.00", [{"id": 10, "product_id": 111, "title": "Sterling", "quantity": 1}]),
        _order(2, "50.00", [{"id": 20, "product_id": 222, "title": "Carnelian", "quantity": 2}]),
    ]
    m = compute_daily_metrics("2026-05-29", orders, handle_map, resolver=RESOLVER)

    assert m.num_orders == 2
    assert m.revenue == 150.0
    # COGS: 76.54*1 + 3*2 = 82.54
    assert round(m.cogs_total, 2) == 82.54
    # spedizione: 7 * 2 = 14
    assert m.shipping_total == 14.0
    # fee: 0.075 * 150 = 11.25
    assert round(m.payment_fees, 2) == 11.25
    # operativo = 150 - 82.54 - 14 - 11.25 - 0 = 42.21
    assert round(m.net_profit_operativo, 2) == 42.21
    # costi fissi giornalieri = 5668/30 = 188.933...
    assert round(m.fixed_cost_daily, 2) == 188.93
    # netto = 42.21 - 188.93 = -146.72
    assert round(m.net_profit_netto, 2) == -146.72
    # AOV = 150/2 = 75
    assert m.aov == 75.0


def test_cancelled_orders_excluded():
    handle_map = {222: "carnelian-signet-ring"}
    orders = [
        _order(1, "50.00", [{"id": 20, "product_id": 222, "title": "Carnelian", "quantity": 1}], cancelled=True),
    ]
    m = compute_daily_metrics("2026-05-29", orders, handle_map, resolver=RESOLVER)
    assert m.num_orders == 0
    assert m.revenue == 0.0
