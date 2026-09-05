"""
Break-even CONTRIBUTION + PROFIT (pooled), quota costi fissi datata e riconciliazione
del net di Sep 4. Deterministico, nessuna rete.
"""
from src.metrics.fixed_costs import daily_fixed_allocation
from src.metrics.profit import (
    compute_breakeven,
    compute_breakeven_full,
    compute_daily_metrics,
    order_revenue,
)


# --------------------------------------------------------------- revenue definition
def test_order_revenue_uses_current_total_price_dated_on_order():
    # current_total_price = totale ATTUALE (netto dei rimborsi propri) -> è la revenue.
    o = {"current_total_price": "84.00", "total_price": "100.00"}   # rimborso di 16 sull'ordine
    assert order_revenue(o) == 84.0
    # fallback a total_price se current_total_price assente (API vecchia)
    assert order_revenue({"total_price": "100.00"}) == 100.0
    assert order_revenue({"current_total_price": "", "total_price": "50.00"}) == 50.0


def test_daily_metrics_revenue_is_current_total_price():
    orders = [
        {"id": 1, "current_total_price": "84.00", "total_price": "100.00", "line_items": []},
        {"id": 2, "total_price": "40.00", "line_items": []},   # nessun current -> fallback 40
    ]
    m = compute_daily_metrics("2026-09-04", orders, {})
    assert round(m.revenue, 2) == 124.0   # 84 (netto) + 40, datati sull'ordine


# --------------------------------------------------------------------------- #3 POOLED
def test_breakeven_uses_pooled_totals_not_avg_of_daily_aov():
    # day1 AOV 100 (1 ordine), day2 AOV 25 (4 ordini). Media dei daily AOV = 62.5;
    # POOLED = 200/5 = 40. Il break-even DEVE usare il pooled.
    rows = [
        {"day": "2026-09-01", "revenue": 100.0, "num_orders": 1, "cogs_total": 0.0},
        {"day": "2026-09-02", "revenue": 100.0, "num_orders": 4, "cogs_total": 0.0},
    ]
    be_roas, be_cpa = compute_breakeven(rows)
    # pooled: AOV 40 − cogs 0 − fee(7.5%·40=3) − spedizione 7 = 30 ; roas 40/30 = 1.333
    assert round(be_cpa, 2) == 30.0
    assert round(be_roas, 3) == 1.333
    # se avesse usato la media dei daily AOV (62.5) sarebbe stato be_cpa ~50.81: escluso
    assert be_cpa < 40


# ------------------------------------------------------------- #4 CONTRIBUTION + PROFIT
def _sep_1_4_rows():
    # Dati VERIFICATI Sep 1–4: revenue 11637.11, ordini 106, COGS 2407.21.
    revenues = [2810.80, 2427.62, 3376.73, 3021.96]        # somma 11637.11
    orders = [27, 27, 26, 26]                               # somma 106
    cogs = [600.00, 600.00, 600.00, 607.21]                # somma 2407.21
    days = ["2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04"]
    return [{"day": d, "revenue": r, "num_orders": o, "cogs_total": c}
            for d, r, o, c in zip(days, revenues, orders, cogs)]


def test_verified_sep_1_4_pooled_inputs():
    rows = _sep_1_4_rows()
    total_rev = sum(r["revenue"] for r in rows)
    total_orders = sum(r["num_orders"] for r in rows)
    total_cogs = sum(r["cogs_total"] for r in rows)
    assert round(total_rev, 2) == 11637.11
    assert total_orders == 106
    assert round(total_cogs, 2) == 2407.21
    assert round(total_rev / total_orders, 2) == 109.78          # AOV
    assert round(total_cogs / total_orders, 2) == 22.71          # COGS/order


def test_contribution_and_profit_breakeven_verified():
    full = compute_breakeven_full(_sep_1_4_rows())
    # contribution (esclude i costi fissi)
    assert round(full["cpa"], 2) == 71.84
    assert round(full["roas"], 2) == 1.53
    # profit (include la quota fissa/ordine: fixed = 4×404.5257 ÷ 106 = 15.27)
    assert round(full["profit_cpa"], 2) == 56.58
    assert round(full["profit_roas"], 2) == 1.94
    assert round(full["avg_orders_per_day"], 1) == 26.5          # 106 / 4 giorni


# ------------------------------------------------------------------ #1 FIXED SCHEDULE
def test_dated_fixed_allocation_new_september_entry():
    # Nuova voce: 12135.77/mese dal 2026-09-01 -> 404.53/giorno.
    assert round(daily_fixed_allocation("2026-09-04"), 2) == 404.53
    assert round(daily_fixed_allocation("2026-09-01"), 2) == 404.53
    # la storia resta invariata
    assert round(daily_fixed_allocation("2026-08-31"), 2) == round(6117 / 30, 2)   # 203.90
    assert round(daily_fixed_allocation("2026-06-15"), 2) == round(7666 / 30, 2)   # 255.53


def test_sep4_net_and_margin_with_new_fixed():
    # Sep 4 VERIFICATO: operating 824.97, revenue 3021.96.
    operating = 824.97
    revenue = 3021.96
    fixed = daily_fixed_allocation("2026-09-04")
    assert round(fixed, 2) == 404.53
    net = operating - fixed
    assert abs(net - 420.45) <= 0.02                             # ~420.44/420.45 (arrotondamenti)
    assert round(net / revenue * 100, 1) == 13.9
