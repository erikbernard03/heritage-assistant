"""
Caricamento e risoluzione della tabella COGS (config/cogs.yaml).

Il match prodotto->costo avviene per HANDLE Shopify (preferito) o per titolo.
REGOLA DI SICUREZZA: qualsiasi prodotto non elencato o con costo 0 => default_cogs ($3).

Questo modulo è puro/deterministico: nessun LLM tocca i numeri.
"""
from __future__ import annotations

import re
from functools import lru_cache
from typing import Optional

import yaml

from config import settings


def _slugify(value: str) -> str:
    """Normalizza un titolo in qualcosa di confrontabile con un handle Shopify."""
    value = (value or "").strip().lower()
    value = re.sub(r"['’]", "", value)        # apostrofi via
    value = re.sub(r"[^a-z0-9]+", "-", value)  # non alfanumerici -> trattino
    return value.strip("-")


class CogsResolver:
    """Risolve il COGS (USD) di un line item a partire da handle/titolo."""

    def __init__(self, yaml_path: Optional[str] = None):
        self.yaml_path = yaml_path or settings.COGS_YAML_PATH
        self._raw = self._load()
        self.default_cogs = float(self._raw.get("default_cogs", 3) or 3)
        self.shipping_per_order = float(self._raw.get("shipping_per_order", 7))
        self.payment_fee_rate = float(self._raw.get("payment_fee_rate", 0.075))

        fixed = self._raw.get("fixed_costs_monthly", {}) or {}
        self.fixed_costs_monthly_total = float(fixed.get("total", 0) or 0)
        self.include_fixed_costs = bool(
            self._raw.get("include_fixed_costs_in_net_profit", True)
        )

        # Mappa unica handle -> costo, costruita da tutte le sezioni del file.
        # Le sezioni esplicite (classic_rings, bracelets) coprono già la regola
        # per collection senza bisogno di interrogare le collection su Shopify.
        self._handle_to_cost: dict[str, float] = {}
        for section in ("custom_products", "classic_rings", "bracelets"):
            for handle, cost in (self._raw.get(section) or {}).items():
                self._handle_to_cost[handle.strip().lower()] = float(cost)

        # Regole per FAMIGLIA (ordinate): (lista_keyword_lowercase, costo).
        # Valutate tra il match esatto (handle/titolo) e il default.
        self._title_rules: list[tuple[list[str], float]] = []
        for rule in (self._raw.get("title_rules") or []):
            kws = [str(k).strip().lower() for k in (rule.get("keywords") or [])]
            if kws and rule.get("cogs") is not None:
                self._title_rules.append((kws, float(rule["cogs"])))

    def _load(self) -> dict:
        with open(self.yaml_path, "r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}

    def _match_title_rules(self, handle: Optional[str], title: Optional[str]) -> Optional[float]:
        """Prima regola title_rules i cui token (keyword) sono TUTTI in handle+titolo."""
        tokens = set(
            (_slugify(handle or "") + "-" + _slugify(title or "")).split("-")
        )
        tokens.discard("")
        for kws, cost in self._title_rules:
            if all(kw in tokens for kw in kws):
                return cost
        return None

    def cogs_for_handle(
        self, handle: Optional[str], title: Optional[str] = None
    ) -> float:
        """
        Restituisce il COGS in USD per un prodotto.

        Priorità:
          1. match esatto per handle (custom_products / classic_rings / bracelets)
          2. match per titolo "slugificato" (stesso elenco esatto)
          3. title_rules (match per FAMIGLIA via keyword su handle+titolo)
          4. default_cogs ($3)
        Inoltre: se il costo trovato è 0 => default_cogs (regola di sicurezza).
        """
        cost: Optional[float] = None

        if handle:
            cost = self._handle_to_cost.get(handle.strip().lower())

        if cost is None and title:
            cost = self._handle_to_cost.get(_slugify(title))

        if cost is None:
            cost = self._match_title_rules(handle, title)

        if cost is None or cost == 0:
            return self.default_cogs
        return float(cost)


@lru_cache(maxsize=1)
def get_resolver() -> CogsResolver:
    """Singleton del resolver (il file YAML si carica una sola volta)."""
    return CogsResolver()
