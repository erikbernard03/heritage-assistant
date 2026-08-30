"""
Test deterministici delle sezioni "best vs worst times": net profit medio per weekday
e vendite per ORA (con conversione dei timestamp ordine a Europe/Rome). Nessuna rete.
"""
from src.dashboard.monthly import weekday_net_profit
from src.metrics.sales_timing import revenue_by_hour, sales_by_hour_by_month


# ---- Weekday net profit (media, non somma) ----
def test_weekday_net_profit_averages_per_weekday():
    # 2 lunedì (2026-08-03, 2026-08-10) e 1 martedì (2026-08-04)
    rows = [
        {"day": "2026-08-03", "net_profit_netto": 100.0},   # lunedì
        {"day": "2026-08-10", "net_profit_netto": 300.0},   # lunedì
        {"day": "2026-08-04", "net_profit_netto": 50.0},    # martedì
    ]
    wp = {d["name"]: d for d in weekday_net_profit(rows)}
    # lunedì: media (100+300)/2 = 200, count 2
    assert round(wp["Mon"]["avg"], 2) == 200.0 and wp["Mon"]["count"] == 2
    # martedì: 50, count 1
    assert round(wp["Tue"]["avg"], 2) == 50.0 and wp["Tue"]["count"] == 1
    # giorni senza dati -> avg None, count 0
    assert wp["Sun"]["avg"] is None and wp["Sun"]["count"] == 0
    # 7 giorni sempre presenti, ordinati lun→dom
    names = [d["name"] for d in weekday_net_profit(rows)]
    assert names == ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


# ---- Sales by hour — conversione a Roma ----
def test_revenue_by_hour_converts_to_rome():
    # created_at in UTC 21:30 = 23:30 a Roma (CEST, +02:00) d'estate -> ora 23
    orders = [
        {"total_price": "100.00", "created_at": "2026-08-19T21:30:00Z"},   # Roma 23
        {"total_price": "50.00", "created_at": "2026-08-19T21:45:00+00:00"},  # Roma 23
        {"total_price": "30.00", "created_at": "2026-08-19T08:00:00+02:00"},  # Roma 08
        {"total_price": "999.00", "cancelled_at": "x",
         "created_at": "2026-08-19T08:00:00+02:00"},                          # cancellato -> escluso
    ]
    by = revenue_by_hour(orders, tz_name="Europe/Rome")
    assert by[23] == {"revenue": 150.0, "orders": 2}
    assert by[8] == {"revenue": 30.0, "orders": 1}
    assert 999.0 not in [v["revenue"] for v in by.values()]


def test_revenue_by_hour_offset_timestamp_maps_to_local_hour():
    # created_at con offset diverso: 2026-08-19T14:30:00-04:00 = 18:30 UTC = 20:30 Roma -> 20
    orders = [{"total_price": "10.00", "created_at": "2026-08-19T14:30:00-04:00"}]
    by = revenue_by_hour(orders, tz_name="Europe/Rome")
    assert by[20] == {"revenue": 10.0, "orders": 1}


def test_sales_by_hour_by_month_groups():
    rows = [
        {"day": "2026-07-31", "hour": 23, "revenue": 100.0, "orders": 1},
        {"day": "2026-08-01", "hour": 23, "revenue": 200.0, "orders": 2},
        {"day": "2026-08-02", "hour": 23, "revenue": 50.0, "orders": 1},
        {"day": "2026-08-02", "hour": 9, "revenue": 40.0, "orders": 1},
    ]
    by = sales_by_hour_by_month(rows)
    assert by["2026-07"][23] == {"revenue": 100.0, "orders": 1}
    assert by["2026-08"][23] == {"revenue": 250.0, "orders": 3}
    assert by["2026-08"][9] == {"revenue": 40.0, "orders": 1}
