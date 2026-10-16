"""
Cache Klaviyo persistente (L2 Supabase) + read-through della dashboard. Deterministico, no rete.
"""
import time

import src.metrics.klaviyo_cache as app


def _kp(camp_rev=100.0, flows_rev=20.0):
    return {
        "ok": True, "error": None,
        "campaigns_revenue": camp_rev,
        "campaigns": [{"campaign_name": "A", "revenue": camp_rev}],
        "flows_revenue": flows_rev,
        "flows": [{"flow_name": "F", "revenue": flows_rev}],
        "daily": {"day": "2026-10-16", "revenue": camp_rev},
    }


class _MemStore:
    """SupabaseStore finto: cache Klaviyo in memoria, mima get/upsert_klaviyo_month_cache."""
    def __init__(self):
        self.rows = {}   # month -> (kp, fetched_at_epoch)
        self.writes = 0

    def get_klaviyo_month_cache(self, month):
        if month not in self.rows:
            return None
        kp, ts = self.rows[month]
        return {"kp": kp, "ts": ts}

    def upsert_klaviyo_month_cache(self, month, kp):
        self.writes += 1
        self.rows[month] = (kp, time.time())


def _patch(monkeypatch, mem, fetch_calls):
    app._KLA_STORE.clear()

    def _fake_fetch(start, end):
        fetch_calls.append((start, end))
        return _kp()
    monkeypatch.setattr(app, "_default_fetch", _fake_fetch)
    monkeypatch.setattr(app, "_db_get",
                        lambda month: ({"data": mem.rows[month][0], "ts": mem.rows[month][1]}
                                       if month in mem.rows else None))
    monkeypatch.setattr(app, "_db_put", lambda month, kp: mem.upsert_klaviyo_month_cache(month, kp))


def test_cold_start_fetches_then_writes_db(monkeypatch):
    mem = _MemStore(); calls = []
    _patch(monkeypatch, mem, calls)
    kp, stale, err = app.get_klaviyo("2026-10-01", "2026-10-16", is_current=True)
    assert kp["campaigns_revenue"] == 100.0 and not stale and err is None
    assert len(calls) == 1                 # ha chiamato Klaviyo una volta
    assert "2026-10" in mem.rows           # scritto in DB


def test_past_month_served_from_db_never_fetches(monkeypatch):
    mem = _MemStore(); calls = []
    # simula riga DB già presente per un mese PASSATO
    mem.rows["2026-09"] = (_kp(camp_rev=555.0), time.time() - 999999)  # vecchissima, ma passato
    _patch(monkeypatch, mem, calls)
    kp, stale, err = app.get_klaviyo("2026-09-01", "2026-09-30", is_current=False)
    assert kp["campaigns_revenue"] == 555.0 and not stale
    assert calls == []                     # MAI fetch per i mesi passati


def test_current_month_fresh_db_no_fetch(monkeypatch):
    mem = _MemStore(); calls = []
    mem.rows["2026-10"] = (_kp(camp_rev=200.0), time.time() - 60)  # fresca (<6h)
    _patch(monkeypatch, mem, calls)
    kp, stale, err = app.get_klaviyo("2026-10-01", "2026-10-16", is_current=True)
    assert kp["campaigns_revenue"] == 200.0 and not stale and calls == []


def test_current_month_stale_db_refetches(monkeypatch):
    mem = _MemStore(); calls = []
    mem.rows["2026-10"] = (_kp(camp_rev=1.0), time.time() - 7 * 3600)  # oltre 6h
    _patch(monkeypatch, mem, calls)
    kp, stale, err = app.get_klaviyo("2026-10-01", "2026-10-16", is_current=True)
    assert kp["campaigns_revenue"] == 100.0  # rifetchato (fake ritorna 100)
    assert len(calls) == 1 and not stale


def test_current_month_stale_served_when_refetch_fails(monkeypatch):
    mem = _MemStore(); calls = []
    mem.rows["2026-10"] = (_kp(camp_rev=42.0), time.time() - 7 * 3600)

    def _boom(start, end):
        calls.append((start, end)); raise RuntimeError("rate limited")
    _patch(monkeypatch, mem, calls)
    monkeypatch.setattr(app, "_default_fetch", _boom)
    kp, stale, err = app.get_klaviyo("2026-10-01", "2026-10-16", is_current=True)
    assert kp["campaigns_revenue"] == 42.0 and stale and "rate limited" in err


def test_store_cache_roundtrip_splits_campaign_and_flow():
    """get/upsert_klaviyo_month_cache: split campaign|flow e ricomposizione corretta."""
    from datetime import datetime, timezone

    captured = {}

    class _Tbl:
        def __init__(self, name): self.name = name
        def select(self, *_a): self._op = "select"; return self
        def eq(self, *_a): return self
        def execute(self):
            return type("R", (), {"data": captured.get("rows", [])})()
        def delete(self): self._op = "delete"; return self
        def insert(self, rows): captured["rows"] = rows; return self

    class _Client:
        def table(self, name): return _Tbl(name)

    from src.db.supabase_client import SupabaseStore
    s = SupabaseStore.__new__(SupabaseStore)
    s.client = _Client()
    s.upsert_klaviyo_month_cache("2026-10", _kp(camp_rev=300.0, flows_rev=50.0))
    kinds = {r["kind"]: r["payload"] for r in captured["rows"]}
    assert kinds["campaign"]["campaigns_revenue"] == 300.0
    assert kinds["flow"]["flows_revenue"] == 50.0
    # ricomposizione
    hit = s.get_klaviyo_month_cache("2026-10")
    assert hit["kp"]["campaigns_revenue"] == 300.0 and hit["kp"]["flows_revenue"] == 50.0


# ----------------------------------------------- warm-up una-tantum dei mesi passati
class _WarmStore:
    def __init__(self, months_with_data, cached):
        self._months = list(months_with_data)
        self._cached = set(cached)
        self.written = []

    def get_daily_metrics_range(self, start, end):
        return [{"day": f"{m}-15"} for m in self._months]

    def get_klaviyo_cached_months(self):
        return set(self._cached)

    def upsert_klaviyo_month_cache(self, month, kp):
        self.written.append(month)
        self._cached.add(month)


def _patch_report_klaviyo(monkeypatch):
    import src.report as R
    from config import settings as S
    monkeypatch.setattr(S, "KLAVIYO_API_KEY", "x")
    monkeypatch.setattr(R, "load_klaviyo_period", lambda s, e: _kp())
    return R


def test_warm_fills_only_missing_past_months(monkeypatch):
    from datetime import date
    R = _patch_report_klaviyo(monkeypatch)
    store = _WarmStore(["2026-07", "2026-08", "2026-09", "2026-10"], cached={"2026-08"})
    out = R.warm_klaviyo_past_months(store=store, today=date(2026, 10, 16))
    # 08 già in cache (saltato), 10 è il mese corrente (escluso) -> riempie 07 e 09
    assert store.written == ["2026-07", "2026-09"]
    assert all(status == "done" for _m, status in out)


def test_warm_is_idempotent_second_night_noop(monkeypatch):
    from datetime import date
    R = _patch_report_klaviyo(monkeypatch)
    store = _WarmStore(["2026-07", "2026-09"], cached={"2026-07", "2026-09"})
    out = R.warm_klaviyo_past_months(store=store, today=date(2026, 10, 16))
    assert store.written == [] and out == []        # tutto già in cache -> nessun fetch


def test_warm_caps_per_night(monkeypatch):
    from datetime import date
    R = _patch_report_klaviyo(monkeypatch)
    store = _WarmStore(["2026-06", "2026-07", "2026-08", "2026-09"], cached=set())
    out = R.warm_klaviyo_past_months(store=store, today=date(2026, 10, 16), max_new=2)
    assert store.written == ["2026-06", "2026-07"] and len(out) == 2   # resto: notti successive
