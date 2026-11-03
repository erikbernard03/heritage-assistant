"""
/backfill deve SMETTERE di dire ✅ quando una tabella per-store NON è stata salvata.
Verifica che un fallimento di una persist-helper finisca nel 7° elemento della tupla
(day_failed) di backfill_daily_metrics — la base del ⚠️ mostrato da cmd_backfill.
"""
import src.report as report


class _FakeShop:
    """Shop Shopify finto: nessuna rete, giorno senza ordini."""
    def get_orders(self, start, end):
        return []

    def get_products_handle_map(self):
        return {}

    def currency_to_usd(self, amount, currency=None):
        return amount

    def get_sessions(self, day):
        return None


class _FakeStore:
    """Store finto: tutto no-op TRANNE upsert_product_units, che ESPLODE."""
    def upsert_daily_metrics(self, m): pass
    def upsert_orders(self, orders, handle_map): pass
    def upsert_line_items(self, m): pass
    def upsert_product_units(self, day, units, store="com"):
        raise RuntimeError("boom: product_units insert rifiutato")
    def upsert_sales_by_country(self, day, data, store="com"): pass
    def upsert_sales_by_hour(self, day, data, store="com"): pass
    def upsert_orders_by_source(self, day, data, store="com"): pass
    def upsert_refunds_daily(self, day, agg, store="com"): pass
    def upsert_store_daily(self, day, label, m): pass


def test_backfill_surfaces_persist_failure(monkeypatch):
    # un solo store: niente .co
    monkeypatch.setattr(report.ShopifyConnector, "for_store_2", staticmethod(lambda: None))

    rows = report.backfill_daily_metrics(
        "2026-10-05", "2026-10-05", shop=_FakeShop(), store=_FakeStore()
    )

    assert len(rows) == 1
    row = rows[0]
    assert row[0] == "2026-10-05"
    assert row[1] != "ERR"                 # il giorno NON è un errore totale...
    day_failed = row[6]                    # ...ma product_units risulta NON salvata
    # ogni voce è "nome_tabella (messaggio d'errore)": prefisso tabella + errore incluso
    assert any(e.startswith("product_units/com") for e in day_failed)
    # l'errore REALE è riportato nella voce (non solo il nome tabella)
    assert any("boom" in e for e in day_failed)
    # le altre tabelle non sono finite per sbaglio nella lista dei falliti
    assert not any(e.startswith("sales_by_hour/com") for e in day_failed)
    assert not any(e.startswith("store_daily/com") for e in day_failed)


def test_backfill_store_daily_failure_reports_real_error(monkeypatch):
    # Riproduce il caso reale: store_daily fallisce (es. colonna mancante) -> il messaggio
    # d'errore REALE deve comparire nella voce day_failed, non solo "store_daily/com".
    monkeypatch.setattr(report.ShopifyConnector, "for_store_2", staticmethod(lambda: None))

    class _StoreDailyFails(_FakeStore):
        def upsert_product_units(self, day, units, store="com"): pass  # ok
        def upsert_store_daily(self, day, label, m):
            raise RuntimeError("Could not find the 'tax_total' column of 'store_daily' in the schema cache")

    rows = report.backfill_daily_metrics(
        "2026-10-05", "2026-10-05", shop=_FakeShop(), store=_StoreDailyFails()
    )
    day_failed = rows[0][6]
    entry = next((e for e in day_failed if e.startswith("store_daily/com")), None)
    assert entry is not None
    assert "tax_total" in entry and "schema cache" in entry


def test_backfill_clean_day_has_empty_failures(monkeypatch):
    monkeypatch.setattr(report.ShopifyConnector, "for_store_2", staticmethod(lambda: None))

    class _OkStore(_FakeStore):
        def upsert_product_units(self, day, units, store="com"): pass  # ora va

    rows = report.backfill_daily_metrics(
        "2026-10-05", "2026-10-05", shop=_FakeShop(), store=_OkStore()
    )
    assert rows[0][6] == []                 # nessun fallimento -> nessun ⚠️
