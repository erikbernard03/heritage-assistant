"""
get_table_range: paginazione deterministica (Supabase tronca a 1000 righe) — deve restituire
TUTTE le righe. Nessuna rete: fake client che simula il cap a pagine.
"""
from src.db.supabase_client import SupabaseStore


class _FakeQuery:
    """Simula la query PostgREST: filtra per day-range, ordina, e TAGLIA a 1000 righe/pagina."""
    def __init__(self, rows):
        self._rows = rows
        self._lo = None
        self._hi = None
        self._order = []
        self._range = None

    def select(self, *_a):
        return self

    def gte(self, col, val):
        if col == "day":
            self._lo = val
        return self

    def lte(self, col, val):
        if col == "day":
            self._hi = val
        return self

    def order(self, col):
        self._order.append(col)
        return self

    def range(self, a, b):
        self._range = (a, b)
        return self

    def execute(self):
        rows = [r for r in self._rows
                if (self._lo is None or r["day"] >= self._lo)
                and (self._hi is None or r["day"] <= self._hi)]
        for col in reversed(self._order):
            rows = sorted(rows, key=lambda r: r.get(col))
        a, b = self._range if self._range else (0, 999)
        page = rows[a:b + 1]
        # IL CAP: PostgREST non restituisce mai più di 1000 righe per richiesta.
        page = page[:1000]
        return type("R", (), {"data": page})()


class _FakeClient:
    def __init__(self, rows):
        self._rows = rows

    def table(self, _name):
        return _FakeQuery(self._rows)


def _store_with(rows):
    s = SupabaseStore.__new__(SupabaseStore)
    s.client = _FakeClient(rows)
    return s


def test_get_table_range_paginates_past_1000_rows():
    # 2500 righe product_units su più giorni/store/product_key: la vecchia select singola
    # ne restituiva solo 1000; ora devono tornare TUTTE e 2500.
    rows = []
    for i in range(2500):
        rows.append({
            "day": f"2026-08-{(i % 28) + 1:02d}",
            "store": "com" if i % 2 == 0 else "co",
            "product_key": f"p{i}",   # univoco -> ordine totale stabile
            "units": 1,
        })
    s = _store_with(rows)
    got = s.get_table_range("product_units_daily", "2026-08-01", "2026-08-31")
    assert len(got) == 2500                         # niente troncamento a 1000
    # nessun duplicato né buco: product_key univoci tutti presenti
    assert len({r["product_key"] for r in got}) == 2500


def test_get_table_range_small_table_single_page():
    rows = [{"day": "2026-10-01", "store": "com", "hour": h, "orders": 1} for h in range(24)]
    s = _store_with(rows)
    got = s.get_table_range("sales_by_hour_daily", "2026-10-01", "2026-10-31")
    assert len(got) == 24


def test_get_table_range_filters_by_day_window():
    rows = ([{"day": "2026-08-15", "store": "com", "product_key": "a", "units": 1}]
            + [{"day": "2026-10-05", "store": "com", "product_key": "b", "units": 2}])
    s = _store_with(rows)
    got = s.get_table_range("product_units_daily", "2026-10-01", "2026-10-31")
    assert [r["day"] for r in got] == ["2026-10-05"]
