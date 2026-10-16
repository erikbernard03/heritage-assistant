"""
Robustezza della persistenza notturna (anti-fallimento-silenzioso):
- un errore su UNA tabella non impedisce le altre (niente cascade-abort);
- _persist RITORNA le tabelle fallite;
- il report Telegram mostra una riga ⚠️ che le NOMINA.
Deterministico, nessuna rete.
"""
import src.report as R
from src.metrics.profit import DailyMetrics


class _Store:
    """Store finto: ogni upsert registra la chiamata; quelli in `fail` sollevano."""
    def __init__(self, fail=()):
        self.fail = set(fail)
        self.calls = []

    def _maybe(self, name):
        self.calls.append(name)
        if name in self.fail:
            raise RuntimeError(f"boom {name}")

    def upsert_orders(self, orders, handle_map): self._maybe("orders")
    def upsert_line_items(self, m): self._maybe("line_items")
    def upsert_daily_metrics(self, m): self._maybe("daily_metrics")
    def upsert_product_units(self, day, units, store="com"): self._maybe(f"product_units/{store}")
    def upsert_sales_by_country(self, day, by, store="com"): self._maybe(f"sales_by_country/{store}")
    def upsert_sales_by_hour(self, day, by, store="com"): self._maybe(f"sales_by_hour/{store}")
    def upsert_orders_by_source(self, day, by, store="com"): self._maybe(f"orders_by_source/{store}")
    def upsert_refunds_daily(self, day, agg, store="com"): self._maybe(f"refunds_daily/{store}")
    def upsert_store_daily(self, day, store, m): self._maybe(f"store_daily/{store}")


def _metrics():
    m = DailyMetrics(day="2026-10-10")
    m.num_orders = 3
    m.revenue = 300.0
    return m


def _stores():
    m = _metrics()
    return [("com", [], {}, m)]


def _patch_store(monkeypatch, store):
    import src.db.supabase_client as sc
    monkeypatch.setattr(sc, "SupabaseStore", lambda: store)
    # niente rete: tw_pixel/stripe no-op
    monkeypatch.setattr(R, "_persist_tw_pixel", lambda *a, **k: None)
    monkeypatch.setattr(R, "_persist_stripe", lambda *a, **k: None)


def test_one_table_failure_does_not_block_the_others(monkeypatch):
    # store_daily FALLISCE ma product_units & co. vengono comunque scritte (no cascade).
    store = _Store(fail={"store_daily/com"})
    _patch_store(monkeypatch, store)
    failed = R._persist(_metrics(), _stores())
    assert failed == ["store_daily/com"]
    # product_units è stato chiamato PRIMA e NON è stato saltato dall'errore di store_daily
    assert "product_units/com" in store.calls
    assert "sales_by_country/com" in store.calls and "refunds_daily/com" in store.calls


def test_product_units_failure_is_reported_not_silent(monkeypatch):
    store = _Store(fail={"product_units/com"})
    _patch_store(monkeypatch, store)
    failed = R._persist(_metrics(), _stores())
    assert "product_units/com" in failed
    # le altre tabelle sono comunque passate
    assert "store_daily/com" in store.calls


def test_per_store_tables_persist_before_stripe(monkeypatch):
    # Le tabelle per-store devono essere scritte PRIMA della rete lenta (Stripe), così il
    # watchdog non le lascia a metà. Verifichiamo l'ordine delle chiamate.
    order = []
    store = _Store()
    store._maybe = lambda name: order.append(name)  # type: ignore
    import src.db.supabase_client as sc
    monkeypatch.setattr(sc, "SupabaseStore", lambda: store)
    monkeypatch.setattr(R, "_persist_tw_pixel", lambda *a, **k: order.append("tw_pixel") or None)
    monkeypatch.setattr(R, "_persist_stripe", lambda *a, **k: order.append("stripe") or None)
    R._persist(_metrics(), _stores())
    assert order.index("product_units/com") < order.index("stripe")
    assert order.index("store_daily/com") < order.index("stripe")


def test_report_surfaces_persist_warning_line():
    line = R._persist_warning_line(["product_units/com", "store_daily/com"])
    assert "Persist issue" in line
    assert "product_units/com" in line and "store_daily/com" in line
    assert R._persist_warning_line([]) == "" and R._persist_warning_line(None) == ""
