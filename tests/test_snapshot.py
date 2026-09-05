"""
Test deterministici degli snapshot single-day /today e /yesterday.
Nessuna rete: la matematica usa compute_daily_metrics + il resolver reale; il wiring
(header, provisional, break-even del giorno stesso) si testa monkeypatchando _gather_day.
"""
from datetime import datetime

import pytz

from src.config_loader import CogsResolver
from src.metrics.profit import DailyMetrics, compute_daily_metrics
from src.report import (
    GatheredDay,
    _own_day_breakeven,
    build_today_snapshot,
    build_yesterday_snapshot,
    format_snapshot,
)

RESOLVER = CogsResolver()


def _order(oid, total, title, qty=1):
    return {"id": oid, "total_price": str(total), "cancelled_at": None,
            "line_items": [{"id": oid * 10, "title": title, "quantity": qty}]}


def _gathered(m):
    return GatheredDay(metrics=m, meta_daily=None, meta_campaigns=[], tiktok_daily=None,
                       tiktok_campaigns=[], google_daily=None, klaviyo_daily=None,
                       klaviyo_campaigns=[])


def _two_gold_signet_day():
    # 2 ordini, 1 gold round signet ciascuno (COGS 12); rev 100 + 50 = 150
    orders = [_order(1, 100, "Personalized Gold Plated Signet Ring"),
              _order(2, 50, "Personalized Gold Plated Signet Ring")]
    return compute_daily_metrics("2026-08-19", orders, {}, resolver=RESOLVER)


def test_own_day_breakeven_from_that_days_numbers():
    m = _two_gold_signet_day()
    # AOV 75, COGS/order 12 -> be_cpa = 75 - 12 - 0.075*75 - 7 = 50.375 ; roas = 75/50.375
    be = _own_day_breakeven(m)
    assert round(be["cpa"], 3) == 50.375
    assert round(be["roas"], 6) == round(75 / 50.375, 6)


def test_own_day_breakeven_zero_orders_is_none():
    m = compute_daily_metrics("2026-08-19", [], {}, resolver=RESOLVER)
    be = _own_day_breakeven(m)
    assert be["roas"] is None and be["cpa"] is None
    assert be["profit_roas"] is None and be["profit_cpa"] is None


def test_snapshot_math_and_layout():
    m = _two_gold_signet_day()
    text = format_snapshot(_gathered(m), _own_day_breakeven(m),
                           "📊 *Today so far — 2026-08-19 14:30 Rome* _(USD)_",
                           provisional=True)
    assert "Today so far — 2026-08-19 14:30 Rome" in text
    assert "💰 Revenue: *$150.00*" in text
    assert "🛒 Orders: *2*" in text
    assert "🧾 AOV: $75.00" in text
    assert "🏷️ COGS: $24.00 ($12.00/order)" in text
    # gross profit = revenue − COGS = 150 − 24 = 126
    assert "📦 Gross profit (rev − COGS): *$126.00*" in text
    # net operating = 150 − 24 − 14(ship) − 11.25(fee) = 100.75
    assert "operating *$100.75*" in text
    # net netto = 100.75 − 203.90(fixed) = −103.15
    assert "net *$-103.15*" in text
    # break-even dal giorno stesso: 75/50.375 = 1.49x ; CPA $50.38
    assert "⚖️ Break-even ROAS: 1.49x · CPA: $50.38 (own day)" in text
    assert "Profit break-even" not in text
    # sezioni + nota provvisoria
    assert "*2) COST BREAKDOWN*" in text
    assert "Fixed-costs allocation (full day): −$203.90" in text
    assert "today's ad attribution is provisional" in text


def test_snapshot_zero_orders_shows_na_and_no_provisional_note():
    m = compute_daily_metrics("2026-08-18", [], {}, resolver=RESOLVER)
    text = format_snapshot(_gathered(m), _own_day_breakeven(m),
                           "📊 *Yesterday — 2026-08-18* _(USD)_", provisional=False)
    assert "🛒 Orders: *0*" in text
    assert "🧾 AOV: $0.00" in text
    assert "📦 Gross profit (rev − COGS): *$0.00*" in text
    assert "⚖️ Break-even ROAS: n/a · CPA: n/a (own day)" in text
    assert "_No ad-platform data yet._" in text
    assert "provisional" not in text          # nessuna nota per ieri


def test_snapshot_platform_lines_and_provisional_flag():
    m = _two_gold_signet_day()
    g = GatheredDay(
        metrics=m,
        meta_daily={"spend": 40.0, "revenue": 120.0, "roas": 3.0, "orders": 2, "cpa": 20.0},
        meta_campaigns=[], tiktok_daily=None, tiktok_campaigns=[], google_daily=None,
        klaviyo_daily={"revenue": 15.0}, klaviyo_campaigns=[],
    )
    text = format_snapshot(g, _own_day_breakeven(m), "H", provisional=True)
    # riga compatta piattaforma in sezione 3
    assert "📣 Meta — spend $40.00 · rev $120.00 · ROAS 3.00x · 2 purch" in text
    # riga ROAS/CPA in sezione 1 marcata provvisoria
    assert "📣 Meta — ROAS 3.00x · CPA $20.00 · provisional" in text
    assert "✉️ Klaviyo campaign revenue: $15.00" in text


def test_build_today_snapshot_wiring(monkeypatch):
    m = _two_gold_signet_day()
    monkeypatch.setattr("src.report._gather_day", lambda window, persist: _gathered(m))
    now = pytz.timezone("Europe/Rome").localize(datetime(2026, 8, 19, 14, 30))
    text = build_today_snapshot(now=now)
    assert "Today so far — 2026-08-19 14:30 Rome" in text
    assert "today's ad attribution is provisional" in text
    # break-even del giorno stesso (own day), non 4-day
    assert "(own day)" in text


def test_build_yesterday_snapshot_wiring(monkeypatch):
    m = _two_gold_signet_day()
    captured = {}

    def _fake_gather(window, persist):
        captured["persist"] = persist
        captured["day"] = window.day_str
        return _gathered(m)

    monkeypatch.setattr("src.report._gather_day", _fake_gather)
    now = pytz.timezone("Europe/Rome").localize(datetime(2026, 8, 19, 9, 0))
    text = build_yesterday_snapshot(now=now)
    assert "Yesterday — 2026-08-18" in text
    assert captured["day"] == "2026-08-18"
    assert captured["persist"] is True          # ieri completo -> refresh canonico
    assert "provisional" not in text
