"""
Classificazione dei line item Shopify in FAMIGLIE di prodotto (unità vendute).

Usa la STESSA meccanica di matching di config/cogs.yaml (title_rules): tokenizzazione
via _slugify di handle+titolo e match per sottoinsieme di keyword, ordine = prima regola
che vince. Qui però l'output è una CHIAVE di prodotto (non un costo), con distinzioni più
fini richieste dalla dashboard (gold vs white-gold round; square; sterling; ecc.).

Codice puro/deterministico: nessun LLM tocca questi numeri.
"""
from __future__ import annotations

from typing import Optional

from src.config_loader import _slugify, get_resolver

# Chiavi canoniche + etichette leggibili (ordine di visualizzazione nella dashboard).
PRODUCT_KEYS: list[tuple[str, str]] = [
    ("gold_signet_round", "Gold plated signet (round)"),
    ("white_gold_signet_round", "White gold plated signet (round)"),
    ("square_signet", "Square signets"),
    ("sterling_silver_signet", "Sterling silver signet"),
    ("coat_of_arms_bracelet", "Coat of arms bracelet"),
    ("coat_of_arms_necklace", "Coat of arms necklace"),
    ("wooden_box", "Wooden box"),
    ("size_adjuster", "Size adjuster"),
    ("classic_stone_ring", "Classic/stone rings"),
    ("other", "Other"),
]
PRODUCT_KEY_LABELS: dict[str, str] = dict(PRODUCT_KEYS)

# Regole ordinate: (keyword_richieste, keyword_escluse, chiave). Prima che matcha vince.
# Stesso principio di cogs.yaml (token-subset), con esclusioni per separare le varianti.
_RULES: list[tuple[list[str], list[str], str]] = [
    (["coat", "arms", "necklace"], [], "coat_of_arms_necklace"),
    (["coat", "arms", "bracelet"], [], "coat_of_arms_bracelet"),
    (["sterling", "signet"], [], "sterling_silver_signet"),
    (["square", "signet"], [], "square_signet"),
    (["white", "gold", "signet"], ["square"], "white_gold_signet_round"),
    (["gold", "signet"], ["square", "white"], "gold_signet_round"),
    (["wooden", "box"], [], "wooden_box"),
    (["size", "adjuster"], [], "size_adjuster"),
]


def _tokens(handle: Optional[str], title: Optional[str]) -> set:
    toks = set((_slugify(handle or "") + "-" + _slugify(title or "")).split("-"))
    toks.discard("")
    return toks


def classify_product_key(
    handle: Optional[str], title: Optional[str] = None, resolver=None
) -> str:
    """
    Classifica un line item nella sua famiglia di prodotto (una PRODUCT_KEYS).

    Priorità:
      1. regole per keyword (ordinate, prima che matcha vince) su token di handle+titolo
      2. handle noto tra i classic_rings di cogs.yaml -> 'classic_stone_ring'
      3. 'other'
    """
    toks = _tokens(handle, title)
    for req, exc, key in _RULES:
        if all(k in toks for k in req) and not any(k in toks for k in exc):
            return key

    resolver = resolver or get_resolver()
    h = (handle or "").strip().lower()
    if h and h in resolver.classic_ring_handles:
        return "classic_stone_ring"
    # match anche per titolo slugificato (handle assente)
    if _slugify(title or "") in resolver.classic_ring_handles:
        return "classic_stone_ring"
    return "other"


def units_by_key_from_line_items(line_items, resolver=None) -> dict[str, int]:
    """
    Somma le UNITÀ (quantity) per chiave di prodotto da una lista di LineItemCost
    (o dict con handle/title/quantity). Ritorna {product_key: units} (solo chiavi >0).
    """
    resolver = resolver or get_resolver()
    out: dict[str, int] = {}
    for li in line_items:
        handle = getattr(li, "handle", None) if not isinstance(li, dict) else li.get("handle")
        title = getattr(li, "title", None) if not isinstance(li, dict) else li.get("title")
        qty = getattr(li, "quantity", None) if not isinstance(li, dict) else li.get("quantity")
        try:
            qty = int(qty or 0)
        except (TypeError, ValueError):
            qty = 0
        if qty <= 0:
            continue
        key = classify_product_key(handle, title, resolver=resolver)
        out[key] = out.get(key, 0) + qty
    return out
