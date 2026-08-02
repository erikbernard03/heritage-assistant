"""
Test deterministici del calcolo net profit (nessuna rete, nessuna credenziale).

Verificano che la matematica torni esattamente, secondo la formula della spec.
"""
from src.config_loader import CogsResolver
from src.metrics.profit import compute_breakeven, compute_daily_metrics

RESOLVER = CogsResolver()  # usa config/cogs.yaml reale


def _order(order_id, total, line_items, cancelled=False):
    return {
        "id": order_id,
        "total_price": total,
        "cancelled_at": "2026-05-29T10:00:00Z" if cancelled else None,
        "line_items": line_items,
    }


def test_cogs_resolution():
    # handle custom noto (sterling: 76.54 -> 48)
    assert RESOLVER.cogs_for_handle("personalized-sterling-silver-signet-ring") == 48
    # classic ring -> 3
    assert RESOLVER.cogs_for_handle("carnelian-signet-ring") == 3
    # sconosciuto -> default 3
    assert RESOLVER.cogs_for_handle("prodotto-inesistente-xyz") == 3
    # match per titolo quando l'handle non è noto
    assert RESOLVER.cogs_for_handle(None, "Carnelian Signet Ring") == 3


def test_cogs_title_rules_family_match():
    r = RESOLVER
    # variante gold signet ROUND NON elencata negli handle -> 12 (era 17.21)
    assert r.cogs_for_handle(
        "personalized-gold-plated-signet-ring-raised-black-paint",
        "Personalized Gold Plated Signet Ring (Raised, Black Paint)",
    ) == 12
    # variante white gold SQUARE -> 17.21 (regola square PRIMA del generico signet)
    assert r.cogs_for_handle(
        "personalized-white-gold-plated-square-signet-ring",
        "Personalized White Gold Plated Square Signet Ring",
    ) == 17.21
    # Coat of Arms Bracelet -> 8 (era 32)
    assert r.cogs_for_handle(
        "personalized-coat-of-arms-bracelet", "Personalized Coat of Arms Bracelet"
    ) == 8
    # Sterling variante (non in custom_products) -> 48, NON 12 (regola prima del generico)
    assert r.cogs_for_handle(
        "personalized-sterling-silver-signet-ring-engraved",
        "Personalized Sterling Silver Signet Ring Engraved",
    ) == 48
    # Ring Size Adjuster Kit -> 1
    assert r.cogs_for_handle("ring-size-adjuster-kit", "Ring Size Adjuster Kit") == 1
    # classic/stone ring (NON personalized) -> 3 (non aggancia la regola signet)
    assert r.cogs_for_handle("amethyst-signet-ring", "Amethyst Signet Ring") == 3


def test_cogs_verification_all_products():
    """Verifica esplicita del mapping richiesto (ordine: first match wins)."""
    r = RESOLVER
    cases = [
        # (handle, title, expected)
        ("personalized-coat-of-arms-necklace",
         "Personalized Coat of Arms Necklace", 8),
        ("personalized-coat-of-arms-bracelet",
         "Personalized Coat of Arms Bracelet", 8),
        ("personalized-sterling-silver-signet-ring",
         "Personalized Sterling Silver Signet Ring", 48),
        ("personalized-gold-plated-square-signet-ring",
         "Personalized Gold Plated Square Signet Ring", 17.21),
        ("personalized-white-gold-plated-square-signet-ring",
         "Personalized White Gold Plated Square Signet Ring", 17.21),
        ("personalized-gold-plated-signet-ring",
         "Personalized Gold Plated Signet Ring", 12),
        ("personalized-white-gold-plated-signet-ring",
         "Personalized White Gold Plated Signet Ring", 12),
        # wooden box ed extra -> default 3
        ("personalized-wooden-ring-box", "Personalized Wooden Ring Box", 3),
    ]
    for handle, title, expected in cases:
        assert r.cogs_for_handle(handle, title) == expected, (handle, title)
        # match anche solo per titolo (handle assente)
        assert r.cogs_for_handle(None, title) == expected, ("title-only", title)


def test_daily_metrics_basic():
    # 2 ordini: uno personalized sterling (48) + uno classic (3)
    handle_map = {111: "personalized-sterling-silver-signet-ring", 222: "carnelian-signet-ring"}
    orders = [
        _order(1, "100.00", [{"id": 10, "product_id": 111, "title": "Sterling", "quantity": 1}]),
        _order(2, "50.00", [{"id": 20, "product_id": 222, "title": "Carnelian", "quantity": 2}]),
    ]
    m = compute_daily_metrics("2026-05-29", orders, handle_map, resolver=RESOLVER)

    assert m.num_orders == 2
    assert m.revenue == 150.0
    # COGS: 48*1 + 3*2 = 54.0
    assert round(m.cogs_total, 2) == 54.0
    # spedizione: 7 * 2 = 14
    assert m.shipping_total == 14.0
    # fee: 0.075 * 150 = 11.25
    assert round(m.payment_fees, 2) == 11.25
    # operativo = 150 - 54 - 14 - 11.25 - 0 = 70.75
    assert round(m.net_profit_operativo, 2) == 70.75
    # costi fissi giornalieri = 6117/30 = 203.90
    assert round(m.fixed_cost_daily, 2) == 203.90
    # netto = 70.75 - 203.90 = -133.15
    assert round(m.net_profit_netto, 2) == -133.15
    # AOV = 150/2 = 75
    assert m.aov == 75.0


def test_cancelled_orders_excluded():
    handle_map = {222: "carnelian-signet-ring"}
    orders = [
        _order(1, "50.00", [{"id": 20, "product_id": 222, "title": "Carnelian", "quantity": 1}], cancelled=True),
    ]
    m = compute_daily_metrics("2026-05-29", orders, handle_map, resolver=RESOLVER)
    assert m.num_orders == 0
    assert m.revenue == 0.0


def test_vat_and_shipping_counted_once_in_net_profit():
    """IVA + spedizione sono dentro total_price -> contano UNA volta nel net profit."""
    handle_map = {222: "carnelian-signet-ring"}
    # total_price 60 = prodotto 50 + IVA 6 + spedizione 4
    order = {
        "id": 1, "total_price": "60.00", "cancelled_at": None,
        "total_tax": "6.00",
        "total_shipping_price_set": {"shop_money": {"amount": "4.00"}},
        "line_items": [{"id": 20, "product_id": 222, "title": "Carnelian", "quantity": 1}],
    }
    m = compute_daily_metrics("2026-05-29", [order], handle_map, resolver=RESOLVER)

    assert m.revenue == 60.0                  # total_price invariato (include IVA+spedizione)
    # net profit usa revenue=total_price UNA sola volta:
    # 60 − 3(COGS) − 7(ship cost) − 4.5(fee) = 45.5  (IVA/spedizione NON aggiunte di nuovo)
    assert round(m.payment_fees, 2) == 4.5
    assert round(m.net_profit_operativo, 2) == 45.5
    # i campi income (split) sommano esattamente a revenue -> nessun doppio conteggio
    assert round(m.product_revenue + m.shipping_collected + m.tax_collected, 2) == 60.0


def test_shipping_collected_fallback_to_shipping_lines():
    handle_map = {222: "carnelian-signet-ring"}
    order = {
        "id": 1, "total_price": "55.00", "cancelled_at": None, "total_tax": "0",
        "shipping_lines": [{"price": "5.00"}],  # niente total_shipping_price_set
        "line_items": [{"id": 20, "product_id": 222, "title": "Carnelian", "quantity": 1}],
    }
    m = compute_daily_metrics("2026-05-29", [order], handle_map, resolver=RESOLVER)
    assert m.shipping_collected == 5.0
    assert m.tax_collected == 0.0


def test_compute_breakeven_4day_avg():
    """FEATURE 1: break-even ROAS/CPA dalla media (aggregata) dei giorni precedenti."""
    # 4 giorni: totali revenue=400, ordini=4, cogs=40  -> AOV=100, COGS/ordine=10
    rows = [
        {"revenue": 100, "num_orders": 1, "cogs_total": 10},
        {"revenue": 100, "num_orders": 1, "cogs_total": 10},
        {"revenue": 100, "num_orders": 1, "cogs_total": 10},
        {"revenue": 100, "num_orders": 1, "cogs_total": 10},
    ]
    be_roas, be_cpa = compute_breakeven(rows)
    # break-even CPA = 100 - 10 - 0.075*100 - 7 = 75.5 (invariato)
    assert round(be_cpa, 2) == 75.5
    # break-even ROAS ora sottrae COGS + fee + spedizione: 100 / 75.5
    assert round(be_roas, 4) == round(100 / 75.5, 4)
    # ROAS == AOV / CPA (coerenti)
    assert round(be_roas, 6) == round(100 / be_cpa, 6)


def test_compute_breakeven_insufficient_data():
    assert compute_breakeven([]) == (None, None)
    assert compute_breakeven([{"revenue": 0, "num_orders": 0, "cogs_total": 0}]) == (None, None)


class _FakeStore:
    """Store fittizio: ritorna le righe 'più recenti che esistono' (con buchi)."""

    def __init__(self, rows):
        self._rows = rows

    def get_daily_metrics_before(self, day, limit=4):
        return [r for r in self._rows if r["day"] < day][:limit]


def test_load_breakeven_uses_4_real_days_despite_gaps():
    from src.report import _load_breakeven

    # giorni reali NON contigui (06-06/07/08 mancano): 06-09, 06-05, 06-02, 06-01
    rows = [
        {"day": "2026-06-09", "revenue": 100, "num_orders": 1, "cogs_total": 10},
        {"day": "2026-06-05", "revenue": 100, "num_orders": 1, "cogs_total": 10},
        {"day": "2026-06-02", "revenue": 100, "num_orders": 1, "cogs_total": 10},
        {"day": "2026-06-01", "revenue": 100, "num_orders": 1, "cogs_total": 10},
        {"day": "2026-05-20", "revenue": 999, "num_orders": 9, "cogs_total": 99},  # oltre i 4
    ]
    be_roas, be_cpa = _load_breakeven("2026-06-10", store=_FakeStore(rows))
    # 4 giorni reali aggregati -> AOV 100, COGS/ord 10 -> CPA = 100-10-7.5-7 = 75.5
    assert round(be_cpa, 2) == 75.5
    assert round(be_roas, 6) == round(100 / 75.5, 6)


def test_load_breakeven_single_day_still_works():
    """Anche con un solo giorno reale, calcola (non resta vuoto/None per i buchi)."""
    from src.report import _load_breakeven

    rows = [{"day": "2026-06-09", "revenue": 120, "num_orders": 1, "cogs_total": 12}]
    be_roas, be_cpa = _load_breakeven("2026-06-10", store=_FakeStore(rows))
    assert be_cpa is not None and be_roas is not None


class _FakeShop:
    """Shop fittizio: handle_map fisso + un ordine round-signet per ogni giorno."""

    def __init__(self):
        self.handle_map = {111: "personalized-gold-plated-signet-ring"}

    def get_products_handle_map(self):
        return self.handle_map

    def get_orders(self, start, end):
        # 1 ordine, 1 round signet -> COGS = costo corrente del round signet
        return [{
            "id": 1, "total_price": "100.00", "cancelled_at": None,
            "line_items": [{"id": 10, "product_id": 111,
                            "title": "Personalized Gold Plated Signet Ring", "quantity": 1}],
        }]


class _CaptureStore:
    """Cattura le righe daily_metrics scritte dal backfill."""

    def __init__(self):
        self.written = {}

    def upsert_orders(self, orders, handle_map):
        pass

    def upsert_line_items(self, metrics):
        pass

    def upsert_daily_metrics(self, metrics):
        self.written[metrics.day] = metrics


def test_backfill_recomputes_cogs_from_current_config():
    """
    /backfill RICALCOLA cogs_total dalla config CORRENTE (cogs.yaml su disco) e
    sovrascrive la riga daily_metrics — NON lascia il vecchio valore. Round signet
    ora = 12: per 1 ordine round-signet il backfill deve scrivere cogs_total=12.

    Il backfill costruisce un resolver FRESCO (CogsResolver()) e invalida il singleton
    @lru_cache di get_resolver(): verifichiamo entrambe le cose.
    """
    import src.config_loader as cl
    from src.report import backfill_daily_metrics

    # Scalda il singleton PRIMA: dimostra che dopo il backfill viene invalidato.
    warmed = cl.get_resolver()
    assert cl.get_resolver() is warmed            # cache attiva (stesso oggetto)

    store = _CaptureStore()
    result = backfill_daily_metrics(
        "2026-06-24", "2026-06-24", shop=_FakeShop(), store=store
    )

    # cogs_total scritto = costo CORRENTE del round signet (12), non un valore stale.
    m = store.written["2026-06-24"]
    assert round(m.cogs_total, 2) == 12.0
    # il riepilogo restituito riporta cogs_total + cogs/order
    day, orders, rev, cogs, cogs_po = result[0]
    assert orders == 1 and cogs == 12.0 and cogs_po == 12.0
    # il singleton è stato invalidato dal backfill (nuovo oggetto al prossimo accesso)
    assert cl.get_resolver() is not warmed
