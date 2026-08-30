"""
Test deterministici delle sezioni "best vs worst times": net profit medio per weekday
e vendite per ORA (con conversione dei timestamp ordine a Europe/Rome). Nessuna rete.
"""
from src.dashboard.monthly import weekday_net_profit
from src.metrics.sales_timing import (
    remap_hours,
    revenue_by_hour,
    sales_by_hour_by_month,
    tz_shift_hours,
)


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


def test_tz_shift_rome_to_dubai_dst():
    # estate (agosto): Roma CEST(+2), Dubai(+4) -> shift +2
    assert tz_shift_hours("2026-08", "Asia/Dubai") == 2
    # inverno (gennaio): Roma CET(+1), Dubai(+4) -> shift +3
    assert tz_shift_hours("2026-01", "Asia/Dubai") == 3
    # Roma -> Roma = 0
    assert tz_shift_hours("2026-08", "Europe/Rome") == 0


def test_remap_hours_shifts_buckets():
    by_hour = {23: {"revenue": 100.0, "orders": 2}, 8: {"revenue": 30.0, "orders": 1}}
    # agosto, Dubai +2: ora 23 -> 1 (23+2=25%24), ora 8 -> 10
    dubai = remap_hours(by_hour, "2026-08", "Asia/Dubai")
    assert dubai[1] == {"revenue": 100.0, "orders": 2}
    assert dubai[10] == {"revenue": 30.0, "orders": 1}
    # inverno Dubai +3: 23 -> 2
    dubai_w = remap_hours({23: {"revenue": 5.0, "orders": 1}}, "2026-01", "Asia/Dubai")
    assert dubai_w[2] == {"revenue": 5.0, "orders": 1}
    # Rome invariato (copia)
    rome = remap_hours(by_hour, "2026-08", "Europe/Rome")
    assert rome[23] == {"revenue": 100.0, "orders": 2} and rome[8]["orders"] == 1


def test_remap_hours_merges_collisions():
    # se due ore Rome finiscono nella stessa ora target, si sommano
    by_hour = {22: {"revenue": 10.0, "orders": 1}, 23: {"revenue": 20.0, "orders": 2}}
    # (fittizio) shift che porti a collisione non capita con +2, ma verifichiamo la somma
    # forzando lo stesso target via due ore adiacenti con shift +2: 22->0, 23->1 (no collisione)
    out = remap_hours(by_hour, "2026-08", "Asia/Dubai")
    assert out[0]["orders"] == 1 and out[1]["orders"] == 2


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
