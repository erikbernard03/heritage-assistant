"""
Test della revenue Klaviyo di PERIODO (query a finestra piena, non somma di snapshot).
Nessuna rete: si monkeypatcha load_klaviyo_period per verificare che le viste di periodo
usino il valore a finestra piena invece della somma giornaliera (che sottostima).
"""
from src.metrics.klaviyo import compute_klaviyo_metrics


def test_full_period_query_gives_correct_per_campaign_totals():
    # La query a finestra piena restituisce, per campagna, la revenue attribuita sull'intero
    # periodo (finestra di attribuzione completa) -> combacia con la dashboard Klaviyo.
    raw = [
        {"groupings": {"campaign_id": "c1"},
         "statistics": {"conversion_value": 751.0, "conversions": 5}},
        {"groupings": {"campaign_id": "c2"},
         "statistics": {"conversion_value": 997.0, "conversions": 8}},
    ]
    names = {"c1": "GARY HALBERT LETTER - 13 AUGUST",
             "c2": "AUG W1 – Sun 9 Aug – Graphic – Flash Sale Closer"}
    computed = compute_klaviyo_metrics("2026-08-01 → 2026-08-31", raw, names=names)
    by_name = {c.campaign_name: c.revenue for c in computed.campaigns}
    assert by_name["GARY HALBERT LETTER - 13 AUGUST"] == 751.0
    assert by_name["AUG W1 – Sun 9 Aug – Graphic – Flash Sale Closer"] == 997.0
    assert round(computed.revenue, 2) == 1748.0


def test_render_multiday_uses_full_period_klaviyo_not_summed_daily(monkeypatch):
    """
    Il report di periodo (/report7, /reportmonth, ...) deve mostrare la revenue Klaviyo
    dalla query a finestra piena, NON la somma degli snapshot giornalieri (che sottostima).
    """
    import src.report as report

    daily = [
        {"day": "2026-08-01", "num_orders": 5, "revenue": 500.0, "cogs_total": 50.0,
         "shipping_total": 35.0, "payment_fees": 37.5, "ads_spend": 0.0,
         "fixed_cost_daily": 203.90, "net_profit_operativo": 377.5,
         "net_profit_netto": 173.60, "store_cvr": 0.03},
        {"day": "2026-08-02", "num_orders": 5, "revenue": 500.0, "cogs_total": 50.0,
         "shipping_total": 35.0, "payment_fees": 37.5, "ads_spend": 0.0,
         "fixed_cost_daily": 203.90, "net_profit_operativo": 377.5,
         "net_profit_netto": 173.60, "store_cvr": 0.05},
    ]

    class _FakeStore:
        def get_table_range(self, table, start, end):
            if table == "klaviyo_daily":
                # snapshot giornalieri che SOTTOSTIMANO (somma = 75, non 997)
                return [{"day": "2026-08-01", "revenue": 40.0, "recipients": 0},
                        {"day": "2026-08-02", "revenue": 35.0, "recipients": 0}]
            return []

    # la query a finestra piena restituisce il valore corretto (997), da usare nel report
    monkeypatch.setattr(
        report, "load_klaviyo_period",
        lambda start_day, end_day: (
            {"day": f"{start_day} → {end_day}", "revenue": 997.0, "opens": 0, "clicks": 0,
             "conversions": 8, "recipients": 0, "open_rate": 0.0, "click_rate": 0.0},
            [{"day": f"{start_day} → {end_day}", "campaign_id": "c2",
              "campaign_name": "Flash Sale Closer", "revenue": 997.0, "opens": 0,
              "clicks": 0, "conversions": 8, "recipients": 0,
              "open_rate": 0.0, "click_rate": 0.0}],
        ),
    )

    text = report._render_multiday(daily, _FakeStore(), header="📊 *Test period* _(USD)_")
    assert "Klaviyo campaign revenue: $997.00" in text
    # NON la somma giornaliera sottostimata (40+35=75)
    assert "Klaviyo campaign revenue: $75.00" not in text


def test_render_multiday_keeps_aggregated_klaviyo_when_period_query_unavailable(monkeypatch):
    """Se la query a finestra piena non è disponibile (None), resta il valore aggregato dal DB."""
    import src.report as report

    daily = [
        {"day": "2026-08-01", "num_orders": 5, "revenue": 500.0, "cogs_total": 50.0,
         "shipping_total": 35.0, "payment_fees": 37.5, "ads_spend": 0.0,
         "fixed_cost_daily": 203.90, "net_profit_operativo": 377.5,
         "net_profit_netto": 173.60, "store_cvr": 0.03},
    ]

    class _FakeStore:
        def get_table_range(self, table, start, end):
            if table == "klaviyo_daily":
                return [{"day": "2026-08-01", "revenue": 120.0, "recipients": 0}]
            return []

    monkeypatch.setattr(report, "load_klaviyo_period", lambda s, e: (None, []))
    text = report._render_multiday(daily, _FakeStore(), header="H")
    assert "Klaviyo campaign revenue: $120.00" in text
