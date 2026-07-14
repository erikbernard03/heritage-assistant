"""
Test deterministici delle finestre temporali della dashboard (src.dashboard.periods).
Nessuna rete: `today` è iniettato, così i confini sono verificabili esattamente.
"""
from datetime import date

from src.dashboard import periods


def test_last_7_days_includes_today():
    # mercoledì 2026-06-24 -> [06-18, 06-24] (7 giorni inclusi oggi)
    assert periods.last_7_days(date(2026, 6, 24)) == ("2026-06-18", "2026-06-24")


def test_this_week_starts_monday():
    # 2026-06-24 è un MERCOLEDÌ -> lunedì della settimana = 2026-06-22
    assert date(2026, 6, 24).weekday() == 2          # 0=lun, 2=mer
    assert periods.this_week(date(2026, 6, 24)) == ("2026-06-22", "2026-06-24")


def test_this_week_on_monday_is_single_day():
    # se oggi è lunedì, start == end (inizio settimana = oggi)
    monday = date(2026, 6, 22)
    assert monday.weekday() == 0
    assert periods.this_week(monday) == ("2026-06-22", "2026-06-22")


def test_this_week_on_sunday_spans_full_week():
    sunday = date(2026, 6, 28)
    assert sunday.weekday() == 6
    assert periods.this_week(sunday) == ("2026-06-22", "2026-06-28")


def test_this_month_month_to_date():
    assert periods.this_month(date(2026, 6, 24)) == ("2026-06-01", "2026-06-24")


def test_last_month_full_previous_calendar_month():
    # a giugno -> maggio COMPLETO (31 giorni)
    assert periods.last_month(date(2026, 6, 24)) == ("2026-05-01", "2026-05-31")


def test_last_month_february_non_leap():
    # a marzo 2026 -> febbraio 2026 (non bisestile): 01 -> 28
    assert periods.last_month(date(2026, 3, 15)) == ("2026-02-01", "2026-02-28")


def test_last_month_february_leap_year():
    # a marzo 2028 (bisestile) -> febbraio 2028: 01 -> 29
    assert periods.last_month(date(2028, 3, 10)) == ("2028-02-01", "2028-02-29")


def test_last_month_january_crosses_year_boundary():
    # a gennaio -> dicembre dell'anno precedente
    assert periods.last_month(date(2026, 1, 5)) == ("2025-12-01", "2025-12-31")


def test_all_time_from_epoch_to_today():
    assert periods.all_time(date(2026, 6, 24)) == ("2000-01-01", "2026-06-24")


def test_custom_range_normal_order():
    assert periods.custom_range(date(2026, 6, 1), date(2026, 6, 10)) == (
        "2026-06-01", "2026-06-10")


def test_custom_range_swaps_when_reversed():
    # estremi invertiti -> vengono riordinati (start <= end)
    assert periods.custom_range(date(2026, 6, 10), date(2026, 6, 1)) == (
        "2026-06-01", "2026-06-10")


def test_custom_range_single_day():
    assert periods.custom_range(date(2026, 6, 5), date(2026, 6, 5)) == (
        "2026-06-05", "2026-06-05")


def test_presets_keys_stable():
    # l'ordine/etichette dei preset è quello atteso dalla UI
    assert list(periods.PRESETS.keys()) == [
        "Last 7 days", "This week", "This month", "Last month", "All time",
    ]
