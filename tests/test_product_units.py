"""
Test deterministici della classificazione unità prodotto (src/metrics/product_units.py).
Usa la stessa meccanica keyword di cogs.yaml. Nessuna rete.
"""
from src.metrics.product_units import (
    PRODUCT_KEY_LABELS,
    classify_product_key,
    product_bucket,
    units_by_key_from_line_items,
)


def test_product_bucket_families_keep_key_others_use_title():
    # famiglia nominata -> chiave canonica
    assert product_bucket("personalized-gold-plated-signet-ring",
                          "Personalized Gold Plated Signet Ring") == "gold_signet_round"
    # non-famiglia (bracciale della collection) -> TITOLO reale, non 'other'
    assert product_bucket("cable-bracelet", "Cable Bracelet") == "Cable Bracelet"
    assert product_bucket("faith-black-onyx-pearls", "FAITH Black Onyx Pearls") \
        == "FAITH Black Onyx Pearls"
    # titolo mancante -> resta 'other'
    assert product_bucket("weird-handle", None) == "other"


def test_units_by_key_breaks_out_bracelets_by_title_no_other_bucket():
    line_items = [
        {"handle": "cable-bracelet", "title": "Cable Bracelet", "quantity": 2},
        {"handle": "black-onyx-bead-bracelet", "title": "Bead Bracelet", "quantity": 1},
        {"handle": "personalized-gold-plated-signet-ring",
         "title": "Personalized Gold Plated Signet Ring", "quantity": 3},
    ]
    units = units_by_key_from_line_items(line_items)
    assert units["Cable Bracelet"] == 2
    assert units["Bead Bracelet"] == 1
    assert units["gold_signet_round"] == 3
    assert "other" not in units          # niente secchio generico


def test_classify_all_families():
    cases = [
        # (handle, title, expected_key)
        ("personalized-gold-plated-signet-ring",
         "Personalized Gold Plated Signet Ring", "gold_signet_round"),
        ("personalized-gold-plated-signet-ring-raised-black-paint",
         "Personalized Gold Plated Signet Ring (Raised, Black Paint)", "gold_signet_round"),
        ("personalized-white-gold-plated-signet-ring",
         "Personalized White Gold Plated Signet Ring", "white_gold_signet_round"),
        ("personalized-gold-plated-square-signet-ring",
         "Personalized Gold Plated Square Signet Ring", "square_signet"),
        ("personalized-white-gold-plated-square-signet-ring",
         "Personalized White Gold Plated Square Signet Ring", "square_signet"),
        ("personalized-sterling-silver-signet-ring",
         "Personalized Sterling Silver Signet Ring", "sterling_silver_signet"),
        ("personalized-coat-of-arms-bracelet",
         "Personalized Coat of Arms Bracelet", "coat_of_arms_bracelet"),
        ("personalized-coat-of-arms-necklace",
         "Personalized Coat of Arms Necklace", "coat_of_arms_necklace"),
        ("personalized-wooden-ring-box", "Personalized Wooden Ring Box", "wooden_box"),
        ("ring-size-adjuster-kit", "Ring Size Adjuster Kit", "size_adjuster"),
        ("carnelian-signet-ring", "Carnelian Signet Ring", "classic_stone_ring"),
        ("some-unknown-thing", "Mystery Item", "other"),
    ]
    for handle, title, expected in cases:
        assert classify_product_key(handle, title) == expected, (handle, title)


def test_square_wins_over_white_gold_round():
    # square+signet DEVE vincere anche quando ci sono white+gold (ordine regole)
    assert classify_product_key(
        "x", "White Gold Square Signet") == "square_signet"


def test_gold_round_excludes_white_and_square():
    assert classify_product_key("x", "Gold Signet") == "gold_signet_round"
    assert classify_product_key("x", "White Gold Signet") == "white_gold_signet_round"


def test_classify_by_title_only_when_handle_absent():
    assert classify_product_key(None, "Carnelian Signet Ring") == "classic_stone_ring"
    assert classify_product_key(None, "Personalized Sterling Silver Signet Ring") \
        == "sterling_silver_signet"


def test_units_by_key_sums_quantities():
    line_items = [
        {"handle": "personalized-gold-plated-signet-ring", "title": "Gold Signet", "quantity": 2},
        {"handle": "personalized-gold-plated-signet-ring", "title": "Gold Signet", "quantity": 1},
        {"handle": "personalized-gold-plated-square-signet-ring", "title": "Square", "quantity": 3},
        {"handle": "ring-size-adjuster-kit", "title": "Adjuster", "quantity": 0},  # 0 -> ignorato
    ]
    units = units_by_key_from_line_items(line_items)
    assert units["gold_signet_round"] == 3
    assert units["square_signet"] == 3
    assert "size_adjuster" not in units          # quantity 0 non contato


def test_units_by_key_supports_lineitem_objects():
    class _LI:
        def __init__(self, handle, title, quantity):
            self.handle, self.title, self.quantity = handle, title, quantity

    units = units_by_key_from_line_items([
        _LI("personalized-coat-of-arms-necklace", "Coat of Arms Necklace", 1),
        _LI("personalized-coat-of-arms-bracelet", "Coat of Arms Bracelet", 2),
    ])
    assert units == {"coat_of_arms_necklace": 1, "coat_of_arms_bracelet": 2}


def test_all_keys_have_labels():
    for key in ("gold_signet_round", "square_signet", "other", "classic_stone_ring"):
        assert key in PRODUCT_KEY_LABELS and PRODUCT_KEY_LABELS[key]
