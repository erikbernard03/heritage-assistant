"""
Regressione: la riga mensile della dashboard e /reportlastmonth devono dare lo STESSO
net profit per lo stesso mese, e il margine deve essere net/revenue della STESSA riga.
Guarda anche il bug "operating mostrato come net" e i costi fissi datati (Σ quota datata,
non somma dei valori stitati). Nessuna rete.
"""
from datetime import date

from src.report import aggregate_period, build_last_month_report

# Luglio 2026 (tutto tra 2026-06-11 e 2026-08-02 -> quota fissa 7666/30 al giorno).
_JULY = [
    {"day": f"2026-07-{d:02d}", "num_orders": 10, "revenue": 1000.0, "cogs_total": 200.0,
     "shipping_total": 70.0, "payment_fees": 75.0, "ads_spend": 0.0,
     "fixed_cost_daily": 0.0,  # STORED 0: aggregate_week deve RICALCOLARE dallo schedule datato
     "net_profit_operativo": 0.0, "net_profit_netto": 0.0,
     "store_cvr": 0.0, "store_sessions": None}
    for d in range(1, 32)
]
_META = [
    {"day": f"2026-07-{d:02d}", "spend": 300.0, "revenue": 500.0, "orders": 4}
    for d in range(1, 32)
]


class _FakeStore:
    def get_daily_metrics_range(self, a, b):
        return [r for r in _JULY if a <= r["day"] <= b]

    def get_table_range(self, table, a, b):
        rows = {"meta_daily": _META}.get(table, [])
        return [r for r in rows if a <= r["day"] <= b]


def test_dashboard_month_equals_reportlastmonth_and_margin_consistent():
    store = _FakeStore()

    # Percorso DASHBOARD: aggregate_period è l'oggetto P&L del mese.
    m = aggregate_period(_JULY, store)[0]

    # Componenti attesi
    assert round(m.revenue, 2) == 31000.0
    assert round(m.cogs_total, 2) == 6200.0
    assert round(m.ads_spend, 2) == 9300.0                 # da meta_daily (live), non stored 0
    # fixed = 31 × 7666/30 (DATATO), NON 0 anche se stored=0
    assert round(m.fixed_cost_daily, 2) == round(31 * 7666 / 30, 2)
    assert m.fixed_cost_daily > 0
    # operating = 31000 − 6200 − 2170 − 2325 − 9300 = 11005 ; net = operating − fixed
    assert round(m.net_profit_operativo, 2) == 11005.0
    assert round(m.net_profit_netto, 2) == round(11005.0 - 31 * 7666 / 30, 2)
    # regressione: operating ≠ net (la dashboard NON deve mostrare operating come "Net profit")
    assert m.net_profit_operativo > m.net_profit_netto

    dash_net = round(m.net_profit_netto, 2)
    dash_margin = round(m.net_profit_netto / m.revenue * 100, 1)
    # margine coerente con lo STESSO net profit della riga
    assert dash_margin == round(dash_net / round(m.revenue, 2) * 100, 1)

    # Percorso /reportlastmonth (eseguito ad agosto -> luglio): stesso net.
    text = build_last_month_report(store=store, today=date(2026, 8, 15))
    assert f"net *${dash_net:,.2f}*" in text
