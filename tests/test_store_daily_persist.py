"""
store_daily deve persistere con i NUOVI campi (shipping formula-based + tax_total) e un payload
SEMPRE JSON-serializzabile (niente Decimal/NaN/Inf). Regressione per il caso in cui /backfill
riportava "store_daily NOT saved". Nessuna rete: fake Supabase client cattura il payload.
"""
import json
import math

from src.config_loader import CogsResolver
from src.db.supabase_client import SupabaseStore
from src.metrics.profit import compute_daily_metrics

R = CogsResolver()


class _FakeTable:
    def __init__(self, sink):
        self._sink = sink

    def upsert(self, payload):
        self._sink["payload"] = payload
        return self

    def execute(self):
        return type("Res", (), {"data": [self._sink.get("payload")]})()


class _FakeClient:
    def __init__(self):
        self.sink = {}

    def table(self, _name):
        return _FakeTable(self.sink)


def _store_with_fake():
    s = SupabaseStore.__new__(SupabaseStore)
    s.client = _FakeClient()
    return s


def test_store_daily_payload_has_new_shipping_and_tax_fields_json_safe():
    # Ordine NUOVO (2026-11-01): COGS datato, shipping formula per-ordine, tasse escluse.
    orders = [{
        "id": 1, "current_total_price": "131.00", "current_total_tax": "20.00",
        "cancelled_at": None, "shipping_address": {"country_code": "US"},
        "line_items": [
            {"id": 10, "product_id": 111, "title": "Personalized Gold Plated Signet Ring",
             "variant_title": "9 / Engraved + Diamonds", "quantity": 1},
            {"id": 11, "product_id": 222, "title": "Cable Bracelet", "quantity": 2},
        ],
    }]
    handle_map = {111: "personalized-gold-plated-signet-ring", 222: "cable-bracelet"}
    m = compute_daily_metrics("2026-11-01", orders, handle_map, resolver=R, currency_to_usd=1.0)

    store = _store_with_fake()
    store.upsert_store_daily("2026-11-01", "com", m)
    payload = store.client.sink["payload"]

    # i nuovi campi ci sono
    assert "shipping_total" in payload and "tax_total" in payload
    assert round(payload["shipping_total"], 2) == round(m.shipping_total, 2)   # 9.60 (US, 3 pezzi)
    assert round(payload["tax_total"], 2) == 20.00

    # tutti i numerici sono float/int nativi (niente Decimal) e finiti (niente NaN/Inf)
    for k, v in payload.items():
        if isinstance(v, float):
            assert math.isfinite(v), (k, v)
        assert not isinstance(v, (bytes,)), k
    # e l'intero payload è JSON-serializzabile SENZA NaN (come richiede PostgREST)
    json.dumps(payload, allow_nan=False)


def test_store_daily_zero_orders_store_is_json_safe():
    # Store senza ordini del giorno: payload comunque valido (0.0, nessun NaN).
    m = compute_daily_metrics("2026-11-01", [], {}, resolver=R, currency_to_usd=1.0)
    store = _store_with_fake()
    store.upsert_store_daily("2026-11-01", "co", m)
    payload = store.client.sink["payload"]
    assert payload["shipping_total"] == 0.0 and payload["tax_total"] == 0.0
    json.dumps(payload, allow_nan=False)
