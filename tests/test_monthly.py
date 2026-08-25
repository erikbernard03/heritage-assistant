"""
Test deterministici delle aggregazioni mensili della dashboard (src/dashboard/monthly.py).
Nessuna rete: funzioni pure con dati iniettati.
"""
from src.dashboard.monthly import (
    daily_breakeven_series,
    group_by_month,
    monthly_visitors,
    month_label,
    month_of,
    period_store_cvr,
    units_by_month,
)


def test_month_label_human_readable():
    assert month_label("2026-07") == "July '26"
    assert month_label("2026-08") == "August '26"
    assert month_label("2025-12") == "December '25"
    # input malformato -> fallback alla chiave
    assert month_label("nope") == "nope"


def _drow(day, orders, revenue, *, cvr=0.0, sessions=None, cogs=0.0):
    return {"day": day, "num_orders": orders, "revenue": revenue, "cogs_total": cogs,
            "store_cvr": cvr, "store_sessions": sessions}


def test_month_of_and_group_by_month():
    assert month_of("2026-06-24") == "2026-06"
    rows = [_drow("2026-05-31", 1, 100.0), _drow("2026-06-01", 2, 200.0),
            _drow("2026-06-15", 3, 300.0)]
    by = group_by_month(rows)
    assert list(by.keys()) == ["2026-05", "2026-06"]
    assert [r["day"] for r in by["2026-06"]] == ["2026-06-01", "2026-06-15"]


def test_monthly_visitors_real_when_all_sessions_present():
    rows = [_drow("2026-06-01", 2, 200.0, sessions=500),
            _drow("2026-06-02", 3, 300.0, sessions=700)]
    visitors, est = monthly_visitors(rows)
    assert visitors == 1200.0 and est is False


def test_monthly_visitors_estimated_when_sessions_missing():
    # nessuna sessione reale -> stima = Σ ordini/cvr sui giorni con cvr>0
    rows = [_drow("2026-06-01", 10, 1000.0, cvr=0.02, sessions=None),   # 500 sess
            _drow("2026-06-02", 10, 1000.0, cvr=0.04, sessions=None)]   # 250 sess
    visitors, est = monthly_visitors(rows)
    assert est is True
    assert round(visitors, 2) == 750.0


def test_monthly_visitors_estimated_if_any_day_missing_sessions():
    rows = [_drow("2026-06-01", 10, 1000.0, cvr=0.02, sessions=500),
            _drow("2026-06-02", 10, 1000.0, cvr=0.04, sessions=None)]  # buco -> stima
    _, est = monthly_visitors(rows)
    assert est is True


def test_period_store_cvr_totals_based_not_averaged():
    # 10/0.02=500 + 10/0.04=250 = 750 sess, 20 conv -> 2.667% (NON media 3%)
    rows = [_drow("2026-06-01", 10, 1000.0, cvr=0.02),
            _drow("2026-06-02", 10, 1000.0, cvr=0.04)]
    assert round(period_store_cvr(rows), 6) == round(20 / 750, 6)


def test_units_by_month_sums_per_key():
    rows = [
        {"day": "2026-05-31", "product_key": "gold_signet_round", "units": 2},
        {"day": "2026-06-01", "product_key": "gold_signet_round", "units": 3},
        {"day": "2026-06-02", "product_key": "square_signet", "units": 4},
    ]
    by = units_by_month(rows)
    assert by["2026-05"] == {"gold_signet_round": 2}
    assert by["2026-06"] == {"gold_signet_round": 3, "square_signet": 4}


def test_daily_breakeven_series_uses_prior_4_days_like_report():
    # 4 giorni di lookback identici -> be_cpa = 100-10-7.5-7 = 75.5 ; be_roas = 100/75.5
    lookback = [
        {"day": "2026-06-01", "num_orders": 1, "revenue": 100.0, "cogs_total": 10.0},
        {"day": "2026-06-02", "num_orders": 1, "revenue": 100.0, "cogs_total": 10.0},
        {"day": "2026-06-03", "num_orders": 1, "revenue": 100.0, "cogs_total": 10.0},
        {"day": "2026-06-04", "num_orders": 1, "revenue": 100.0, "cogs_total": 10.0},
        # giorno del periodo: AOV proprio = 120/2 = 60
        {"day": "2026-06-05", "num_orders": 2, "revenue": 120.0, "cogs_total": 20.0},
    ]
    series = daily_breakeven_series(lookback, {"2026-06-05"})
    assert len(series) == 1
    s = series[0]
    assert s["day"] == "2026-06-05"
    assert round(s["aov"], 2) == 60.0                       # AOV del giorno stesso
    assert round(s["be_cpa"], 2) == 75.5                    # dai 4 giorni precedenti
    assert round(s["be_roas"], 4) == round(100 / 75.5, 4)


def test_daily_breakeven_series_none_when_no_priors():
    rows = [{"day": "2026-06-05", "num_orders": 2, "revenue": 120.0, "cogs_total": 20.0}]
    series = daily_breakeven_series(rows, {"2026-06-05"})
    assert series[0]["be_roas"] is None and series[0]["be_cpa"] is None
    assert round(series[0]["aov"], 2) == 60.0


def test_daily_breakeven_series_only_emits_period_days():
    rows = [
        {"day": "2026-06-01", "num_orders": 1, "revenue": 100.0, "cogs_total": 10.0},
        {"day": "2026-06-02", "num_orders": 1, "revenue": 100.0, "cogs_total": 10.0},
    ]
    series = daily_breakeven_series(rows, {"2026-06-02"})
    assert [s["day"] for s in series] == ["2026-06-02"]
