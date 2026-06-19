"""
Test deterministici dell'aggregazione a 7 giorni (aggregate_week) — nessuna rete.
"""
from src.report import aggregate_week, build_weekly_report, format_report


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
    assert round(m.store_cvr, 4) == 0.03                # media dei valori >0: (0.02+0.04)/2
    assert header.startswith("📊 *7-day report — 2026-06-14 → 2026-06-17*")

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
    assert "7-day report — 2026-06-16 → 2026-06-17" in text
    assert "*1) KEY METRICS*" in text
    assert "*2) COST BREAKDOWN*" in text
    assert "Revenue: *$1,000.00*" in text
    assert "Store CVR: 4.00%" in text   # media (0.03+0.05)/2 = 0.04
