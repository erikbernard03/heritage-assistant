"""
COGS DATATO (fatture HyperSKU M26–M29, in vigore dal 2026-11-01).
Stesso prodotto: valore VECCHIO il 2026-10-31, valore NUOVO il 2026-11-01. Nessuna rete.
"""
from src.config_loader import CogsResolver

R = CogsResolver()
OLD = "2026-10-31"
NEW = "2026-11-01"

# (handle, title, old, new)
CASES = [
    ("personalized-gold-plated-signet-ring", "Personalized Gold Plated Signet Ring", 12.0, 11.46),
    ("personalized-white-gold-plated-signet-ring", "Personalized White Gold Plated Signet Ring", 12.0, 11.46),
    ("personalized-silver-plated-signet-ring", "Personalized Silver Plated Signet Ring", 12.0, 11.46),
    ("personalized-sterling-silver-signet-ring", "Personalized Sterling Silver Signet Ring", 48.0, 49.03),
    ("personalized-gold-plated-square-signet-ring", "Personalized Gold Plated Square Signet Ring", 17.21, 8.62),
    ("personalized-white-gold-plated-square-signet-ring", "Personalized White Gold Plated Square Signet Ring", 17.21, 8.62),
    ("personalized-wooden-ring-box", "Personalized Wooden Ring Box", 3.0, 2.82),
    (None, "Matte Dark Brown Jewelry Organizer", 3.0, 2.82),
    ("personalized-coat-of-arms-bracelet", "Personalized Coat of Arms Bracelet", 8.0, 8.27),
    ("personalized-coat-of-arms-necklace", "Personalized Coat of Arms Necklace", 8.0, 8.27),
    (None, "Personalized Cufflinks", 15.0, 14.11),
    ("carnelian-signet-ring", "Carnelian Signet Ring", 3.0, 2.95),
    ("cross-texture-ring", "Cross Texture Ring", 3.0, 3.53),
    ("cable-bracelet", "Cable Bracelet", 3.0, 2.58),
    ("ring-size-adjuster-kit", "Ring Size Adjuster Kit", 1.0, 1.72),
]


def test_old_value_on_2026_10_31_new_value_on_2026_11_01():
    for handle, title, old, new in CASES:
        assert round(R.cogs_for_handle(handle, title, order_date=OLD), 2) == round(old, 2), (handle, title, "OLD")
        assert round(R.cogs_for_handle(handle, title, order_date=NEW), 2) == round(new, 2), (handle, title, "NEW")


def test_no_date_resolves_to_base_current_values():
    # senza data = valori BASE (= pre-2026-11-01), identici a oggi
    assert R.cogs_for_handle("personalized-gold-plated-signet-ring",
                             "Personalized Gold Plated Signet Ring") == 12.0
    assert R.cogs_for_handle(None, "Square Signet Ring") == 17.21


def test_black_onyx_wins_over_generic_signet_families():
    # deve risolvere al valore del Black Onyx (2.97 dal 2026-11-01), MAI alle famiglie signet
    cost, source = R.resolve_with_source("black-onyx-signet-ring", "Black Onyx Signet Ring", order_date=NEW)
    assert cost == 2.97 and source in ("exact-title", "handle")
    assert cost != 11.46 and cost != 8.62
    # anche solo per titolo
    cost2, source2 = R.resolve_with_source(None, "Black Onyx Signet Ring", order_date=NEW)
    assert cost2 == 2.97 and source2 == "exact-title"


def test_fleur_de_lis_bracelet_does_not_catch_preorder_or_signet():
    # la regola Fleur-de-Lis Bracelet (2.58) NON deve toccare il Tile Bracelet (PRE-ORDER) né il signet
    assert R.cogs_for_handle("fleur-de-lis-bracelet-1", "Fleur-de-Lis Bracelet", order_date=NEW) == 2.58
    # PRE-ORDER tile bracelet resta $3.00 (exact-title), NON 2.58
    tile, src = R.resolve_with_source(None, "Fleur-de-Lis Tile Bracelet (PRE-ORDER)", order_date=NEW)
    assert tile == 3.00 and src == "exact-title"
    # Fleur-de-Lis signet (PRE-ORDER) resta $3.00, NON 2.58
    sig, src2 = R.resolve_with_source(None, "Fleur-de-Lis Signet Ring (PRE-ORDER)", order_date=NEW)
    assert sig == 3.00 and src2 == "exact-title"


def test_variant_diamonds_rule_wins_over_product_title_signet():
    # Variante "Engraved + Diamonds" su un signet = 12.92, vince sulla regola del titolo-prodotto.
    cost, source = R.resolve_with_source(
        "personalized-gold-plated-signet-ring", "Personalized Gold Plated Signet Ring",
        order_date=NEW, variant_title="4.5 / Engraved + Diamonds")
    assert cost == 12.92 and source == "variant"
    # senza la variante Diamonds -> torna al prezzo del titolo-prodotto (11.46)
    cost2, _ = R.resolve_with_source(
        "personalized-gold-plated-signet-ring", "Personalized Gold Plated Signet Ring",
        order_date=NEW, variant_title="4.5 / Engraved")
    assert cost2 == 11.46
    # case-insensitive e su altri signet
    cost3, src3 = R.resolve_with_source(
        None, "Personalized White Gold Plated Signet Ring",
        order_date=NEW, variant_title="7 / engraved + DIAMONDS")
    assert cost3 == 12.92 and src3 == "variant"
    # la regola variante NON si applica prima del 2026-11-01 (resta il valore base)
    old, _ = R.resolve_with_source(
        "personalized-gold-plated-signet-ring", "Personalized Gold Plated Signet Ring",
        order_date=OLD, variant_title="4.5 / Engraved + Diamonds")
    assert old == 12.0


def test_new_handles_from_point_3():
    assert R.cogs_for_handle("turquoise-blue-signet-ring", None, order_date=NEW) == 2.95
    assert R.cogs_for_handle("black-stone-square-signet-ring", None, order_date=NEW) == 2.95
    assert R.cogs_for_handle("oval-carnelian-pinky-ring", None, order_date=NEW) == 3.45
    assert R.cogs_for_handle("tiger-s-eye-signet-ring-copy", None, order_date=NEW) == 2.95
    # bead/pearl/edge bracelets restano $3.00
    assert R.cogs_for_handle("black-onyx-bead-bracelet", None, order_date=NEW) == 3.00
    assert R.cogs_for_handle("white-edge-bracelet", None, order_date=NEW) == 3.00
    # Malachite via exact-title (handle malachite-signet-ring)
    assert R.cogs_for_handle("malachite-signet-ring", "Malachite Signet Ring", order_date=NEW) == 2.95


def test_preorder_entries_untouched_on_new_date():
    # i PRE-ORDER (necklaces 3.50, bracelets/signets 3.00) restano invariati dal 2026-11-01
    assert R.cogs_for_handle(None, "Compass Necklace (PRE-ORDER)", order_date=NEW) == 3.50
    assert R.cogs_for_handle(None, "Rope Bracelet (PRE-ORDER)", order_date=NEW) == 3.00
    assert R.cogs_for_handle(None, "Anchor Signet Ring (PRE-ORDER)", order_date=NEW) == 3.00
