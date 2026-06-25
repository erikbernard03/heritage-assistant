"""
Test deterministici dell'aggregazione multi-giorno (aggregate_week) e del margine.
Nessuna rete.
"""
from datetime import date

from src.metrics.profit import DailyMetrics
from src.report import (
    aggregate_week,
    build_month_report,
    build_weekly_report,
    format_report,
)


def test_margin_line_in_key_metrics():
    m = DailyMetrics(day="2026-06-17", num_orders=10, revenue=1000.0,
                     net_profit_operativo=300.0, net_profit_netto=120.0)
    text = format_report(m)
    # operating 300/1000=30.0% · net 120/1000=12.0%
    assert "📊 Margin — operating 30.0% · net 12.0%" in text


def test_margin_line_zero_revenue_na():
    m = DailyMetrics(day="2026-06-17", num_orders=0, revenue=0.0,
                     net_profit_operativo=0.0, net_profit_netto=-255.53)
    assert "📊 Margin — operating n/a · net n/a" in format_report(m)


def test_aggregate_week_totals_and_per_platform():
    # 3 giorni reali (con un buco: 06-15 manca tra 06-14 e 06-16)
    daily = [
        {"day": "2026-06-14", "num_orders": 10, "revenue": 1000.0, "cogs_total": 100.0,
         "shipping_total": 70.0, "payment_fees": 75.0, "ads_spend": 200.0,
         "fixed_cost_daily": 255.53, "net_profit_operativo": 555.0,
         "net_profit_netto": 299.47, "store_cvr": 0.02},
        {"day": "2026-06-16", "num_orders": 5, "revenue": 500.0, "cogs_total": 50.0,
         "shipping_total": 35.0, "payment_fees": 37.5, "ads_spend": 100.0,
         "fixed_cost_daily": 255.53, "net_profit_operativo": 277.5,
         "net_profit_netto": 21.97, "store_cvr": 0.04},
        {"day": "2026-06-17", "num_orders": 5, "revenue": 500.0, "cogs_total": 50.0,
         "shipping_total": 35.0, "payment_fees": 37.5, "ads_spend": 100.0,
         "fixed_cost_daily": 255.53, "net_profit_operativo": 277.5,
         "net_profit_netto": 21.97, "store_cvr": 0.0},
    ]
    meta = [
        {"day": "2026-06-14", "spend": 100.0, "revenue": 400.0, "orders": 4, "roas": 4.0, "cpa": 25.0},
        {"day": "2026-06-16", "spend": 100.0, "revenue": 200.0, "orders": 2, "roas": 2.0, "cpa": 50.0},
    ]
    meta_camp = [
        {"day": "2026-06-14", "campaign_id": "c1", "campaign_name": "Prospecting",
         "spend": 100.0, "revenue": 400.0, "orders": 4},
        {"day": "2026-06-16", "campaign_id": "c1", "campaign_name": "Prospecting",
         "spend": 100.0, "revenue": 200.0, "orders": 2},
    ]
    (m, meta_daily, meta_campaigns, tiktok_daily, google_daily,
     klaviyo_daily, klaviyo_campaigns, breakeven, header) = aggregate_week(
        daily, meta, [], [], [], meta_camp, [],
    )

    # totali daily_metrics
    assert m.num_orders == 20
    assert m.revenue == 2000.0
    assert round(m.aov, 2) == 100.0                     # 2000/20
    assert round(m.cogs_total, 2) == 200.0
    assert round(m.net_profit_operativo, 2) == 1110.0
    assert round(m.fixed_cost_daily, 2) == 766.59       # 255.53 × 3 giorni
    # CVR periodo = Σorders/Σsessions: 10/0.02 + 5/0.04 = 625 sess, 15 conv -> 0.024
    assert round(m.store_cvr, 4) == 0.024
    assert header.startswith("📊 *3-day report — 2026-06-14 → 2026-06-17*")  # 3 giorni reali

    # Meta da TOTALI 7gg: spend 200, rev 600 -> ROAS 3.0 ; orders 6 -> CPA 33.33
    assert round(meta_daily["spend"], 2) == 200.0
    assert round(meta_daily["roas"], 4) == 3.0
    assert round(meta_daily["cpa"], 2) == round(200.0 / 6, 2)
    assert meta_daily["day"] == "2026-06-14 → 2026-06-17"

    # campagna aggregata: spend 200, rev 600, ord 6 -> ROAS 3.0
    assert len(meta_campaigns) == 1
    c = meta_campaigns[0]
    assert round(c["spend"], 2) == 200.0 and c["orders"] == 6
    assert round(c["roas"], 4) == 3.0


def test_build_weekly_report_with_fake_store_renders_layout():
    class _FakeStore:
        def __init__(self, daily):
            self._daily = daily

        def get_recent_daily_metrics(self, days=7):
            return self._daily

        def get_table_range(self, table, start, end):
            return []

    daily = [
        {"day": "2026-06-16", "num_orders": 5, "revenue": 500.0, "cogs_total": 50.0,
         "shipping_total": 35.0, "payment_fees": 37.5, "ads_spend": 0.0,
         "fixed_cost_daily": 255.53, "net_profit_operativo": 377.5,
         "net_profit_netto": 121.97, "store_cvr": 0.03},
        {"day": "2026-06-17", "num_orders": 5, "revenue": 500.0, "cogs_total": 50.0,
         "shipping_total": 35.0, "payment_fees": 37.5, "ads_spend": 0.0,
         "fixed_cost_daily": 255.53, "net_profit_operativo": 377.5,
         "net_profit_netto": 121.97, "store_cvr": 0.05},
    ]
    text = build_weekly_report(store=_FakeStore(daily))
    # header riflette i giorni reali (2)
    assert "2-day report — 2026-06-16 → 2026-06-17" in text
    assert "*1) KEY METRICS*" in text
    assert "*2) COST BREAKDOWN*" in text
    assert "Revenue: *$1,000.00*" in text
    # CVR di periodo = Σorders/Σsessions con sessions=orders/cvr:
    # 5/0.03 + 5/0.05 = 266.67 sess, 10 conv -> 3.75% (NON la media 4.00%)
    assert "Store CVR: 3.75%" in text
    # margine aggregato: operating 755/1000=75.5% · net 243.94/1000=24.4%
    assert "📊 Margin — operating 75.5% · net 24.4%" in text


def test_period_cvr_totals_based_not_summed():
    """5 giorni @ ~0.45% CVR -> periodo ~0.45%, NON ~2.25% (somma)."""
    daily = [
        {"day": d, "num_orders": n, "revenue": 100.0 * n, "cogs_total": 0.0,
         "shipping_total": 0.0, "payment_fees": 0.0, "ads_spend": 0.0,
         "fixed_cost_daily": 0.0, "net_profit_operativo": 0.0,
         "net_profit_netto": 0.0, "store_cvr": 0.0045}
        for d, n in zip(
            ("2026-06-10", "2026-06-11", "2026-06-12", "2026-06-13", "2026-06-14"),
            (8, 12, 5, 20, 9),
        )
    ]
    m = aggregate_week(daily, [], [], [], [], [], [])[0]
    assert round(m.store_cvr * 100, 2) == 0.45     # = tasso giornaliero
    assert m.store_cvr < 0.01                       # sotto l'1%, mai 2.25%


def test_period_cvr_is_weighted_between_min_and_max():
    daily = [
        {"day": "2026-06-10", "num_orders": 10, "revenue": 1000.0, "store_cvr": 0.004,
         "cogs_total": 0.0, "shipping_total": 0.0, "payment_fees": 0.0, "ads_spend": 0.0,
         "fixed_cost_daily": 0.0, "net_profit_operativo": 0.0, "net_profit_netto": 0.0},
        {"day": "2026-06-11", "num_orders": 10, "revenue": 1000.0, "store_cvr": 0.008,
         "cogs_total": 0.0, "shipping_total": 0.0, "payment_fees": 0.0, "ads_spend": 0.0,
         "fixed_cost_daily": 0.0, "net_profit_operativo": 0.0, "net_profit_netto": 0.0},
    ]
    m = aggregate_week(daily, [], [], [], [], [], [])[0]
    # sessions 10/0.004=2500 + 10/0.008=1250 = 3750 ; 20/3750 = 0.005333
    assert round(m.store_cvr, 6) == round(20 / 3750, 6)
    assert 0.004 < m.store_cvr < 0.008             # tra min e max, non somma


def test_report5_limits_to_5_days_and_header():
    """/report5 usa days=5: lo store riceve days=5 e l'header riflette i giorni reali."""
    class _FakeStore:
        def __init__(self):
            self.requested = None

        def get_recent_daily_metrics(self, days=7):
            self.requested = days
            # ritorna 5 giorni reali (con buco 06-15)
            return [
                {"day": d, "num_orders": 4, "revenue": 400.0, "cogs_total": 40.0,
                 "shipping_total": 28.0, "payment_fees": 30.0, "ads_spend": 0.0,
                 "fixed_cost_daily": 255.53, "net_profit_operativo": 302.0,
                 "net_profit_netto": 46.47, "store_cvr": 0.03}
                for d in ("2026-06-13", "2026-06-14", "2026-06-16", "2026-06-17", "2026-06-18")
            ]

        def get_table_range(self, table, start, end):
            return []

    store = _FakeStore()
    text = build_weekly_report(days=5, store=store)
    assert store.requested == 5
    assert "5-day report — 2026-06-13 → 2026-06-18" in text
    assert "Revenue: *$2,000.00*" in text         # 400 × 5
    assert "Orders: *20*" in text


def test_reportmonth_window_first_of_month_to_last_data_day():
    """
    /reportmonth: finestra = 1° del mese corrente (Roma) -> giorno più recente CON dati.
    Lo store riceve get_daily_metrics_range(primo-del-mese, oggi); l'header mostra
    il range fino all'ULTIMO giorno con dati (non 'oggi' se i dati finiscono prima).
    """
    class _FakeStore:
        def __init__(self):
            self.range_args = None

        def get_daily_metrics_range(self, start, end):
            self.range_args = (start, end)
            # 4 giorni reali del mese (con un buco: manca 06-03); l'ultimo dato è 06-23,
            # mentre 'oggi' è 06-25 -> l'header deve fermarsi al 06-23.
            return [
                {"day": d, "num_orders": 10, "revenue": 1000.0, "cogs_total": 100.0,
                 "shipping_total": 70.0, "payment_fees": 75.0, "ads_spend": 0.0,
                 "fixed_cost_daily": 255.53, "net_profit_operativo": 755.0,
                 "net_profit_netto": 499.47, "store_cvr": 0.03}
                for d in ("2026-06-01", "2026-06-02", "2026-06-04", "2026-06-23")
            ]

        def get_table_range(self, table, start, end):
            return []

    store = _FakeStore()
    text = build_month_report(store=store, today=date(2026, 6, 25))
    # la query copre dal 1° del mese fino a OGGI (anche se i dati finiscono prima)
    assert store.range_args == ("2026-06-01", "2026-06-25")
    # l'header si ferma all'ultimo giorno CON dati (06-23), non al 06-25
    assert "Month-to-date report — 2026-06-01 → 2026-06-23" in text
    assert "*1) KEY METRICS*" in text
    assert "*2) COST BREAKDOWN*" in text
    assert "Revenue: *$4,000.00*" in text          # 1000 × 4 giorni
    assert "Orders: *40*" in text
    # costi fissi allocati = 255.53 × 4 giorni = 1022.12
    assert "$1,022.12" in text


def test_reportmonth_no_data_message():
    class _FakeStore:
        def get_daily_metrics_range(self, start, end):
            return []

    text = build_month_report(store=_FakeStore(), today=date(2026, 6, 25))
    assert "Month-to-date report — 2026-06-01 → 2026-06-25" in text
    assert "no data available yet" in text
