"""
Match COGS per TITOLO ESATTO (priorità sulle regole per famiglia). Deterministico, nessuna rete.
Ogni titolo elencato deve risolvere al costo esatto e via layer 'exact-title' (nessuna fuga
nelle famiglie generiche). Un titolo NON elencato deve ancora risolvere via keyword rules.
"""
from src.config_loader import CogsResolver

RESOLVER = CogsResolver()

# (titolo, costo atteso) — esattamente come richiesto dal business.
EXACT = [
    # Necklaces $3.50
    ("Compass Necklace (PRE-ORDER)", 3.50),
    ("Onyx Crown Necklace (PRE-ORDER)", 3.50),
    ("Hammered Cross Necklace (PRE-ORDER)", 3.50),
    ("Turquoise Signet Necklace (PRE-ORDER)", 3.50),
    ("Equestrian Medallion Necklace (PRE-ORDER)", 3.50),
    ("Tutto Passa Necklace (PRE-ORDER)", 3.50),
    ("Con Calma Necklace (PRE-ORDER)", 3.50),
    # Bracelets $3.00
    ("Fleur-de-Lis Tile Bracelet (PRE-ORDER)", 3.00),
    ("Gold Hook Bracelet (PRE-ORDER)", 3.00),
    ("Navy Leather Cuff (PRE-ORDER)", 3.00),
    ("Black Cord Cuff (PRE-ORDER)", 3.00),
    ("Navy Mariner Bracelet (PRE-ORDER)", 3.00),
    ("Black Shackle Bracelet (PRE-ORDER)", 3.00),
    ("Regatta Bracelet (PRE-ORDER)", 3.00),
    ("Rope Bracelet (PRE-ORDER)", 3.00),
    ("Cuban Bracelet (PRE-ORDER)", 3.00),
    # Rings $3.00
    ("Tutto Passa Signet Ring (PRE-ORDER)", 3.00),
    ("Dolce Vita Signet Ring (PRE-ORDER)", 3.00),
    ("Fleur-de-Lis Signet Ring (PRE-ORDER)", 3.00),
    ("Anchor Signet Ring (PRE-ORDER)", 3.00),
    # Cufflinks $3.00
    ("Sea Turtle Cufflinks", 3.00),
    ("Lobster Cufflinks", 3.00),
    ("Black Onyx Cufflinks", 3.00),
    ("Sailboat Cufflinks", 3.00),
    ("Black Striped Cufflinks", 3.00),
    ("Race Car Cufflinks", 3.00),
    ("Four Aces Cufflinks", 3.00),
    ("Airplane Cufflinks", 3.00),
    # Personalized cufflinks $15.00
    ("Personalized Cufflinks", 15.00),
]


def test_every_exact_title_resolves_exact_and_not_via_family():
    for title, expected in EXACT:
        cost, source = RESOLVER.resolve_with_source(None, title)
        assert round(cost, 2) == round(expected, 2), f"{title}: {cost} != {expected}"
        # DEVE arrivare dal layer titolo-esatto (niente fuga nelle famiglie keyword)
        assert source == "exact-title", f"{title} matched via '{source}', not exact-title"


def test_exact_title_is_case_insensitive_and_whitespace_tolerant():
    # maiuscole/minuscole diverse + spazi doppi -> stesso match esatto
    cost, source = RESOLVER.resolve_with_source(None, "  tutto  passa  SIGNET ring (Pre-Order)  ")
    assert round(cost, 2) == 3.00 and source == "exact-title"


def test_signet_preorder_does_not_hit_generic_signet_family():
    # Senza il layer esatto questi rischiavano la famiglia signet: verifichiamo che NON accada.
    cost, source = RESOLVER.resolve_with_source(None, "Tutto Passa Signet Ring (PRE-ORDER)")
    assert cost == 3.00 and source == "exact-title"
    # e il vero signet personalizzato NON è toccato: resta famiglia $12
    cost2, source2 = RESOLVER.resolve_with_source(None, "Personalized Gold Plated Signet Ring")
    assert cost2 == 12.0 and source2 == "family"


def test_non_listed_title_still_uses_keyword_rules():
    # Prodotti NON in exact_titles continuano a risolvere via title_rules (famiglia) o default.
    assert RESOLVER.resolve_with_source(None, "Personalized Coat of Arms Necklace") == (8.0, "family")
    # questo titolo slugifica su un handle elencato (custom_products) -> layer title-slug, $48
    assert RESOLVER.resolve_with_source(None, "Personalized Sterling Silver Signet Ring") == (48.0, "title-slug")
    assert RESOLVER.resolve_with_source(None, "Square Signet Ring") == (17.21, "family")
    # handle esatto ancora valido
    assert RESOLVER.resolve_with_source("personalized-wooden-ring-box", None) == (3.0, "handle")
    # sconosciuto -> default $3
    assert RESOLVER.resolve_with_source(None, "Totally Unknown Widget") == (3.0, "default")
