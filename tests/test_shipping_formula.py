"""
Spedizione per-ordine DATATA (dal 2026-11-01) + flat $7 prima. country_base + piece_surcharge.
Nessuna rete.
"""
from src.config_loader import CogsResolver
from src.metrics.profit import compute_breakeven_full, compute_daily_metrics

R = CogsResolver()
NEW = "2026-11-01"
OLD = "2026-10-31"


def _order(cc, items):
    """items: lista di (titolo, qty)."""
    return {"shipping_address": {"country_code": cc},
            "line_items": [{"title": t, "quantity": q} for t, q in items]}


def test_shipping_matrix():
    cases = [
        (_order("GB", [("Signet Ring", 1)]), NEW, 3.90),
        (_order("US", [("Ring", 2)]), NEW, 8.20),
        (_order("AU", [("Ring", 3)]), NEW, 9.00),
        (_order("IT", [("Ring", 7)]), NEW, 11.30),
        (_order("ZZ", [("Ring", 1)]), NEW, 6.50),                 # unknown country
        ({"line_items": [{"title": "Ring", "quantity": 1}]}, NEW, 6.50),  # missing country
        (_order("GB", [("Ring", 3)]), OLD, 7.00),                 # pre-2026-11-01 flat
    ]
    for order, day, expected in cases:
        assert round(R.shipping_for_order(order, day), 2) == expected, (order, day)


def test_zero_pieces_zero_shipping():
    # ordine di soli articoli non spediti (protezione spedizione) -> 0 pezzi -> $0
    o = _order("US", [("Shipping Protection", 1)])
    assert R.order_pieces(o, NEW) == 0
    assert R.shipping_for_order(o, NEW) == 0.0


def test_pieces_exclude_non_shipped_but_count_box_and_adjuster():
    o = _order("US", [
        ("Personalized Wooden Ring Box", 1),       # conta
        ("Ring Size Adjuster Kit", 1),             # conta
        ("Matte Dark Brown Jewelry Organizer", 1), # conta
        ("Shipping Protection", 1),                # escluso
        ("Gift Card", 3),                          # escluso
        ("Tip", 1),                                # escluso
    ])
    assert R.order_pieces(o, NEW) == 3             # box + adjuster + organizer


def test_shipping_flows_into_daily_metrics():
    # giorno NUOVO: shipping_total = somma formula per-ordine (non più 7×ordini)
    handle_map = {}
    orders = [
        _order("GB", [("Ring", 1)]),               # 3.90
        _order("US", [("Ring", 2)]),               # 8.20
    ]
    for i, o in enumerate(orders):
        o["id"] = i + 1
        o["current_total_price"] = "100.00"
        o["cancelled_at"] = None
    m = compute_daily_metrics(NEW, orders, handle_map, resolver=R)
    assert round(m.shipping_total, 2) == 12.10     # 3.90 + 8.20

    # giorno VECCHIO: flat $7/ordine (invariato)
    for o in orders:
        pass
    m_old = compute_daily_metrics(OLD, orders, handle_map, resolver=R)
    assert round(m_old.shipping_total, 2) == 14.00  # 7 × 2


def test_breakeven_uses_pooled_shipping_from_rows():
    # due giorni con shipping reale: pooled = Σ shipping / Σ ordini
    rows = [
        {"revenue": 200, "num_orders": 2, "cogs_total": 20, "shipping_total": 12.10},
        {"revenue": 100, "num_orders": 1, "cogs_total": 10, "shipping_total": 6.50},
    ]
    be = compute_breakeven_full(rows)
    # AOV=300/3=100; COGS/ord=30/3=10; fee 7.5%·100=7.5; shipping/ord=(12.10+6.50)/3=6.2333
    expected_cpa = 100 - 10 - 7.5 - (18.60 / 3)
    assert round(be["cpa"], 4) == round(expected_cpa, 4)


def test_exclusion_matches_whole_words_only():
    # "route" NON deve agganciare un titolo in cui "route" è dentro una parola più lunga.
    o = _order("US", [
        ("Reroute Signet Ring", 1),      # 'reroute' != 'route' -> CONTA
        ("Scenic Pendant", 1),           # CONTA
    ])
    assert R.order_pieces(o, NEW) == 2
    # mentre "Route" come parola intera (app di insurance) è escluso
    o2 = _order("US", [("Route Protection", 1), ("Signet Ring", 1)])
    assert R.order_pieces(o2, NEW) == 1
    # frase multi-parola "gift card" esclusa come parole intere; "giftcardholder" no
    o3 = _order("US", [("Gift Card", 1), ("Signet Ring", 1)])
    assert R.order_pieces(o3, NEW) == 1


def test_breakeven_fallback_flat7_when_rows_lack_shipping():
    # righe legacy senza shipping_total -> fallback flat $7 (compatibilità)
    rows = [{"revenue": 100, "num_orders": 1, "cogs_total": 10}]
    be = compute_breakeven_full(rows)
    assert round(be["cpa"], 2) == 75.5             # 100 − 10 − 7.5 − 7
