"""
Test deterministici del re-bucketing ORARIO Meta (fuso account -> giorni Europe/Rome).
Nessuna rete: si testano le funzioni pure di src/metrics/meta.py.

Scenario chiave: l'ad account è in Asia/Dubai (UTC+4, no DST). I report usano i giorni
di Europe/Rome (CEST=UTC+2 d'estate, CET=UTC+1 d'inverno). Un acquisto a mezzanotte-1
di Roma (es. 23:30 del giorno X) cade nell'ora successiva di Dubai e va comunque
attribuito al giorno X di Roma.
"""
from src.metrics.meta import (
    HOURLY_BREAKDOWN,
    compute_meta_metrics,
    rebucket_hourly_to_daily_rows,
    rome_day_for_hour,
)


def _hrow(date_start, hour, *, cid="c1", name="Prospecting", spend="0",
          impressions="0", clicks="0", orders=0, revenue=0.0):
    """Costruisce una riga oraria Meta (breakdown nel fuso account)."""
    return {
        "date_start": date_start,
        HOURLY_BREAKDOWN: f"{hour:02d}:00:00 - {hour:02d}:59:59",
        "campaign_id": cid,
        "campaign_name": name,
        "spend": spend,
        "impressions": impressions,
        "clicks": clicks,
        "actions": [{"action_type": "purchase", "value": orders}],
        "action_values": [{"action_type": "purchase", "value": revenue}],
    }


# --------------------------------------------------------------------------- #
# rome_day_for_hour — conversione istante (fuso account) -> giorno Roma
# --------------------------------------------------------------------------- #
def test_rome_day_23h30_rome_purchase_lands_in_correct_rome_day_summer():
    """
    Acquisto alle 23:30 di Roma dell'11 agosto = 01:30 di Dubai del 12 agosto
    (Dubai è 2h avanti a Roma d'estate). Meta lo bucketizza come Dubai 12/08 ora 01;
    ri-bucketato deve tornare al giorno di Roma 2026-08-11.
    """
    assert rome_day_for_hour("2026-08-12", "01:00:00 - 01:59:59",
                             "Asia/Dubai", "Europe/Rome") == "2026-08-11"


def test_rome_day_same_day_when_hour_is_later():
    # Dubai 12/08 03:00 -> Rome 12/08 01:00 -> stesso giorno di Roma
    assert rome_day_for_hour("2026-08-12", "03:00:00 - 03:59:59",
                             "Asia/Dubai", "Europe/Rome") == "2026-08-12"


def test_rome_day_winter_dst_offset_three_hours():
    """
    D'inverno Roma è UTC+1: Dubai è 3h avanti. Un acquisto 23:30 Roma del 15/01 =
    02:30 Dubai del 16/01; ri-bucketato deve tornare a Roma 2026-01-15.
    """
    assert rome_day_for_hour("2026-01-16", "02:00:00 - 02:59:59",
                             "Asia/Dubai", "Europe/Rome") == "2026-01-15"


def test_rome_day_reads_account_tz_dynamically_utc():
    # account già in UTC: nessuno shift di giorno per ore centrali
    assert rome_day_for_hour("2026-08-12", "12:00:00 - 12:59:59",
                             "UTC", "Europe/Rome") == "2026-08-12"


# --------------------------------------------------------------------------- #
# rebucket_hourly_to_daily_rows — aggregazione in giorni di Roma
# --------------------------------------------------------------------------- #
def test_rebucket_splits_hours_into_correct_rome_days():
    rows = [
        # Dubai 12/08 ora 01 -> Rome 11/08 23:00 -> giorno Roma 08-11
        _hrow("2026-08-12", 1, spend="5", impressions="50", clicks="3",
              orders=1, revenue=130.0),
        # Dubai 12/08 ora 03 -> Rome 12/08 01:00 -> giorno Roma 08-12
        _hrow("2026-08-12", 3, spend="7", impressions="70", clicks="4",
              orders=2, revenue=260.0),
    ]
    by_day = rebucket_hourly_to_daily_rows(rows, "Asia/Dubai", "Europe/Rome")

    assert set(by_day.keys()) == {"2026-08-11", "2026-08-12"}
    r11 = by_day["2026-08-11"][0]
    assert r11["spend"] == 5.0 and r11["impressions"] == 50 and r11["clicks"] == 3
    assert r11["actions"][0]["value"] == 1 and r11["action_values"][0]["value"] == 130.0
    r12 = by_day["2026-08-12"][0]
    assert r12["spend"] == 7.0 and r12["action_values"][0]["value"] == 260.0


def test_rebucket_sums_multiple_hours_same_rome_day():
    # due ore Dubai che cadono entrambe nel giorno di Roma 08-12
    rows = [
        _hrow("2026-08-12", 3, spend="2", orders=1, revenue=100.0),   # Rome 01:00 08-12
        _hrow("2026-08-12", 14, spend="8", orders=3, revenue=300.0),  # Rome 12:00 08-12
    ]
    by_day = rebucket_hourly_to_daily_rows(rows, "Asia/Dubai", "Europe/Rome")
    assert list(by_day.keys()) == ["2026-08-12"]
    row = by_day["2026-08-12"][0]
    assert row["spend"] == 10.0
    assert row["actions"][0]["value"] == 4          # 1 + 3
    assert row["action_values"][0]["value"] == 400.0


def test_rebucket_keeps_campaigns_separate():
    rows = [
        _hrow("2026-08-12", 14, cid="c1", name="Prospecting", spend="8", orders=2, revenue=200.0),
        _hrow("2026-08-12", 14, cid="c2", name="Retargeting", spend="4", orders=1, revenue=90.0),
    ]
    by_day = rebucket_hourly_to_daily_rows(rows, "Asia/Dubai", "Europe/Rome")
    day = by_day["2026-08-12"]
    assert len(day) == 2
    by_id = {r["campaign_id"]: r for r in day}
    assert by_id["c1"]["spend"] == 8.0 and by_id["c2"]["spend"] == 4.0


# --------------------------------------------------------------------------- #
# end-to-end: rebucket -> compute_meta_metrics (stesso calcolo di sempre)
# --------------------------------------------------------------------------- #
def test_rebucketed_rows_feed_compute_meta_metrics():
    rows = [
        _hrow("2026-08-12", 1, spend="5", clicks="3", orders=1, revenue=130.0),   # Rome 08-11
        _hrow("2026-08-12", 3, spend="7", clicks="4", orders=2, revenue=260.0),   # Rome 08-12
    ]
    by_day = rebucket_hourly_to_daily_rows(rows, "Asia/Dubai", "Europe/Rome")

    m11 = compute_meta_metrics("2026-08-11", by_day["2026-08-11"], account_currency="USD")
    assert m11.spend == 5.0 and m11.revenue == 130.0 and m11.orders == 1
    assert round(m11.roas, 4) == 26.0 and round(m11.cpa, 2) == 5.0

    m12 = compute_meta_metrics("2026-08-12", by_day["2026-08-12"], account_currency="USD")
    assert m12.spend == 7.0 and m12.orders == 2 and round(m12.cpa, 2) == 3.5


def test_rebucketed_rows_apply_fx_once_for_eur_account():
    from config import settings

    rows = [_hrow("2026-08-12", 14, spend="10", orders=1, revenue=100.0)]  # Rome 08-12
    by_day = rebucket_hourly_to_daily_rows(rows, "Asia/Dubai", "Europe/Rome")
    m = compute_meta_metrics("2026-08-12", by_day["2026-08-12"], account_currency="EUR")
    # fx applicato UNA sola volta in compute_meta_metrics
    assert round(m.spend, 4) == round(10.0 * settings.EUR_TO_USD, 4)
    assert round(m.revenue, 4) == round(100.0 * settings.EUR_TO_USD, 4)
