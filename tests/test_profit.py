"""
Test deterministici del calcolo net profit (nessuna rete, nessuna credenziale).

Verificano che la matematica torni esattamente, secondo la formula della spec.
"""
from src.config_loader import CogsResolver
from src.metrics.profit import compute_breakeven, compute_daily_metrics

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
    # costi fissi giornalieri = 7666/30 = 255.533...
    assert round(m.fixed_cost_daily, 2) == 255.53
    # netto = 42.21 - 255.53 = -213.32
    assert round(m.net_profit_netto, 2) == -213.32
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


def test_vat_and_shipping_counted_once_in_net_profit():
    """IVA + spedizione sono dentro total_price -> contano UNA volta nel net profit."""
    handle_map = {222: "carnelian-signet-ring"}
    # total_price 60 = prodotto 50 + IVA 6 + spedizione 4
    order = {
        "id": 1, "total_price": "60.00", "cancelled_at": None,
        "total_tax": "6.00",
        "total_shipping_price_set": {"shop_money": {"amount": "4.00"}},
        "line_items": [{"id": 20, "product_id": 222, "title": "Carnelian", "quantity": 1}],
    }
    m = compute_daily_metrics("2026-05-29", [order], handle_map, resolver=RESOLVER)

    assert m.revenue == 60.0                  # total_price invariato (include IVA+spedizione)
    # net profit usa revenue=total_price UNA sola volta:
    # 60 − 3(COGS) − 7(ship cost) − 4.5(fee) = 45.5  (IVA/spedizione NON aggiunte di nuovo)
    assert round(m.payment_fees, 2) == 4.5
    assert round(m.net_profit_operativo, 2) == 45.5
    # i campi income (split) sommano esattamente a revenue -> nessun doppio conteggio
    assert round(m.product_revenue + m.shipping_collected + m.tax_collected, 2) == 60.0


def test_shipping_collected_fallback_to_shipping_lines():
    handle_map = {222: "carnelian-signet-ring"}
    order = {
        "id": 1, "total_price": "55.00", "cancelled_at": None, "total_tax": "0",
        "shipping_lines": [{"price": "5.00"}],  # niente total_shipping_price_set
        "line_items": [{"id": 20, "product_id": 222, "title": "Carnelian", "quantity": 1}],
    }
    m = compute_daily_metrics("2026-05-29", [order], handle_map, resolver=RESOLVER)
    assert m.shipping_collected == 5.0
    assert m.tax_collected == 0.0


def test_compute_breakeven_4day_avg():
    """FEATURE 1: break-even ROAS/CPA dalla media (aggregata) dei giorni precedenti."""
    # 4 giorni: totali revenue=400, ordini=4, cogs=40  -> AOV=100, COGS/ordine=10
    rows = [
        {"revenue": 100, "num_orders": 1, "cogs_total": 10},
        {"revenue": 100, "num_orders": 1, "cogs_total": 10},
        {"revenue": 100, "num_orders": 1, "cogs_total": 10},
        {"revenue": 100, "num_orders": 1, "cogs_total": 10},
    ]
    be_roas, be_cpa = compute_breakeven(rows)
    # break-even CPA = 100 - 10 - 0.075*100 - 7 = 75.5 (invariato)
    assert round(be_cpa, 2) == 75.5
    # break-even ROAS ora sottrae COGS + fee + spedizione: 100 / 75.5
    assert round(be_roas, 4) == round(100 / 75.5, 4)
    # ROAS == AOV / CPA (coerenti)
    assert round(be_roas, 6) == round(100 / be_cpa, 6)


def test_compute_breakeven_insufficient_data():
    assert compute_breakeven([]) == (None, None)
    assert compute_breakeven([{"revenue": 0, "num_orders": 0, "cogs_total": 0}]) == (None, None)


class _FakeStore:
    """Store fittizio: ritorna le righe 'più recenti che esistono' (con buchi)."""

    def __init__(self, rows):
        self._rows = rows

    def get_daily_metrics_before(self, day, limit=4):
        return [r for r in self._rows if r["day"] < day][:limit]


def test_load_breakeven_uses_4_real_days_despite_gaps():
    from src.report import _load_breakeven

    # giorni reali NON contigui (06-06/07/08 mancano): 06-09, 06-05, 06-02, 06-01
    rows = [
        {"day": "2026-06-09", "revenue": 100, "num_orders": 1, "cogs_total": 10},
        {"day": "2026-06-05", "revenue": 100, "num_orders": 1, "cogs_total": 10},
        {"day": "2026-06-02", "revenue": 100, "num_orders": 1, "cogs_total": 10},
        {"day": "2026-06-01", "revenue": 100, "num_orders": 1, "cogs_total": 10},
        {"day": "2026-05-20", "revenue": 999, "num_orders": 9, "cogs_total": 99},  # oltre i 4
    ]
    be_roas, be_cpa = _load_breakeven("2026-06-10", store=_FakeStore(rows))
    # 4 giorni reali aggregati -> AOV 100, COGS/ord 10 -> CPA = 100-10-7.5-7 = 75.5
    assert round(be_cpa, 2) == 75.5
    assert round(be_roas, 6) == round(100 / 75.5, 6)


def test_load_breakeven_single_day_still_works():
    """Anche con un solo giorno reale, calcola (non resta vuoto/None per i buchi)."""
    from src.report import _load_breakeven

    rows = [{"day": "2026-06-09", "revenue": 120, "num_orders": 1, "cogs_total": 12}]
    be_roas, be_cpa = _load_breakeven("2026-06-10", store=_FakeStore(rows))
    assert be_cpa is not None and be_roas is not None
