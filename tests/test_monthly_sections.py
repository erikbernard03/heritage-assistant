"""
Test deterministici delle nuove sezioni mensili: costi fissi datati, gross/blended,
goals, vendite per paese, aggregazioni Meta/Google per mese. Nessuna rete.
"""
from src.dashboard.monthly import (
    MONTHLY_GOALS,
    google_by_month,
    goal_progress,
    gross_and_blended,
    meta_campaigns_by_month,
    month_unit_economics,
)
from src.metrics.fixed_costs import daily_fixed_allocation, monthly_fixed_cost_for
from src.metrics.sales_location import (
    country_of,
    revenue_by_country,
    sales_by_country_by_month,
)


# ---- #9 costi fissi datati ----
def test_fixed_cost_schedule_dated():
    assert monthly_fixed_cost_for("2026-05-01") == 5668       # prima del 06-11
    assert monthly_fixed_cost_for("2026-06-10") == 5668       # giorno prima dello switch
    assert monthly_fixed_cost_for("2026-06-11") == 7666       # switch (incluso)
    assert monthly_fixed_cost_for("2026-07-15") == 7666
    assert monthly_fixed_cost_for("2026-08-01") == 7666       # giorno prima dello switch
    assert monthly_fixed_cost_for("2026-08-02") == 6117       # switch (incluso)
    assert monthly_fixed_cost_for("2026-08-31") == 6117       # giorno prima dello switch Sep
    assert monthly_fixed_cost_for("2026-09-01") == 12135.77   # switch (incluso, +stipendio)
    assert monthly_fixed_cost_for("2026-09-30") == 12135.77
    assert round(daily_fixed_allocation("2026-09-04"), 2) == 404.53
    assert round(daily_fixed_allocation("2026-08-19"), 2) == round(6117 / 30, 2)
    assert round(daily_fixed_allocation("2026-05-29"), 2) == round(5668 / 30, 2)


# ---- #8 gross profit & blended ROAS ----
def test_gross_and_blended():
    gross, blended = gross_and_blended(revenue=10000.0, cogs=2500.0, total_ad_spend=4000.0)
    assert gross == 7500.0
    assert round(blended, 4) == 2.5
    # ad spend 0 -> blended None
    g2, b2 = gross_and_blended(1000.0, 100.0, 0.0)
    assert g2 == 900.0 and b2 is None


def test_month_unit_economics_from_month_totals():
    # 100 ordini, revenue 10000 (AOV 100), COGS 1000 (COGS/ordine 10)
    u = month_unit_economics(revenue=10000.0, cogs=1000.0, orders=100)
    assert round(u["aov"], 2) == 100.0
    assert round(u["cogs_per_order"], 2) == 10.0
    # gross/order = (10000-1000)/100 = 90
    assert round(u["gross_per_order"], 2) == 90.0
    # be_cpa = 100 - 10 - 0.075*100 - 7 = 75.5 ; be_roas = 100/75.5
    assert round(u["be_cpa"], 2) == 75.5
    assert round(u["be_roas"], 4) == round(100 / 75.5, 4)


def test_month_unit_economics_zero_orders_is_none():
    u = month_unit_economics(0.0, 0.0, 0)
    assert u["gross_per_order"] is None and u["be_roas"] is None and u["be_cpa"] is None


# ---- #11 goals ----
def test_goal_progress_math():
    # metà mese (giorno 15 su 30), $60k su $124k
    p = goal_progress(revenue_so_far=60000.0, goal=124000.0, day_of_month=15, days_in_month=30)
    assert round(p["pct"], 2) == round(60000 / 124000 * 100, 2)
    assert round(p["needed_per_day"], 2) == round(124000 / 30, 2)
    assert round(p["actual_per_day"], 2) == 4000.0               # 60000/15
    assert round(p["projected"], 2) == 120000.0                  # 4000 * 30
    assert p["on_pace"] is False                                 # 120k < 124k
    assert round(p["remaining"], 2) == 64000.0


def test_goals_table_constants():
    assert MONTHLY_GOALS["2026-09"]["goal"] == 124000
    assert MONTHLY_GOALS["2026-12"]["orders_per_day"] == 115


# ---- #10 sales by country ----
def test_country_of_prefers_shipping_then_billing():
    assert country_of({"shipping_address": {"country_code": "us"}}) == "US"
    assert country_of({"billing_address": {"country_code": "IT"}}) == "IT"
    assert country_of({}) == "Unknown"


def test_revenue_by_country_excludes_cancelled():
    orders = [
        {"total_price": "100.00", "shipping_address": {"country_code": "US"}},
        {"total_price": "50.00", "shipping_address": {"country_code": "US"}},
        {"total_price": "30.00", "shipping_address": {"country_code": "IT"}},
        {"total_price": "999.00", "cancelled_at": "x", "shipping_address": {"country_code": "IT"}},
    ]
    by = revenue_by_country(orders)
    assert by["US"] == {"revenue": 150.0, "orders": 2}
    assert by["IT"] == {"revenue": 30.0, "orders": 1}


def test_sales_by_country_by_month_groups():
    rows = [
        {"day": "2026-07-31", "country": "US", "revenue": 100.0, "orders": 1},
        {"day": "2026-08-01", "country": "US", "revenue": 200.0, "orders": 2},
        {"day": "2026-08-05", "country": "IT", "revenue": 50.0, "orders": 1},
    ]
    by = sales_by_country_by_month(rows)
    assert by["2026-07"]["US"] == {"revenue": 100.0, "orders": 1}
    assert by["2026-08"]["US"] == {"revenue": 200.0, "orders": 2}
    assert by["2026-08"]["IT"] == {"revenue": 50.0, "orders": 1}


# ---- #4 / #5 meta & google by month ----
def test_meta_campaigns_by_month_aggregates():
    rows = [
        {"day": "2026-08-01", "campaign_id": "c1", "campaign_name": "Prospecting",
         "spend": 100.0, "revenue": 300.0, "orders": 3},
        {"day": "2026-08-02", "campaign_id": "c1", "campaign_name": "Prospecting",
         "spend": 100.0, "revenue": 300.0, "orders": 3},
        {"day": "2026-08-02", "campaign_id": "c2", "campaign_name": "Retargeting",
         "spend": 50.0, "revenue": 200.0, "orders": 2},
    ]
    by = meta_campaigns_by_month(rows)
    aug = {c["campaign_name"]: c for c in by["2026-08"]}
    assert aug["Prospecting"]["spend"] == 200.0 and aug["Prospecting"]["revenue"] == 600.0
    assert aug["Prospecting"]["orders"] == 6
    assert round(aug["Prospecting"]["roas"], 4) == 3.0
    assert round(aug["Prospecting"]["cpa"], 4) == round(200.0 / 6, 4)
    assert by["2026-08"][0]["campaign_name"] == "Prospecting"   # ordinato per spend desc


def test_google_by_month_totals():
    rows = [
        {"day": "2026-08-01", "spend": 100.0, "revenue": 250.0, "orders": 2},
        {"day": "2026-08-10", "spend": 100.0, "revenue": 150.0, "orders": 2},
    ]
    g = google_by_month(rows)["2026-08"]
    assert g["spend"] == 200.0 and g["revenue"] == 400.0 and g["orders"] == 4
    assert round(g["roas"], 4) == 2.0
    assert round(g["cpa"], 4) == 50.0
