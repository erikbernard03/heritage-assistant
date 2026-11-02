"""
Caricamento e risoluzione della tabella COGS (config/cogs.yaml) + spedizione per-ordine.

Il match prodotto->costo avviene per HANDLE Shopify (preferito) o per titolo.
REGOLA DI SICUREZZA: qualsiasi prodotto non elencato o con costo 0 => default_cogs ($3).

DATATO: il costo e la spedizione dipendono dalla DATA ROME dell'ordine. I valori base
(top-level del YAML) valgono per ogni data PRIMA del primo overlay; gli overlay in
`cogs_schedule` / `shipping.schedule` valgono dalla rispettiva `effective_from`.

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


def _norm_title(value: str) -> str:
    """Titolo canonico per il match ESATTO: minuscolo + spazi collassati + apostrofi rimossi
    (punteggiatura e parentesi conservate). Es. "Tiger's Eye" -> "tigers eye"; così il match
    esatto è immune al tipo di apostrofo (dritto/curvo) come già fa lo slug."""
    value = re.sub(r"['’]", "", value or "")
    return " ".join(value.split()).lower()


def _to_date_str(order_date) -> Optional[str]:
    """Normalizza una data (str 'YYYY-MM-DD' o date/datetime) nei primi 10 char ISO, o None."""
    if order_date is None:
        return None
    s = str(order_date)
    return s[:10] if s else None


class _CogsPeriod:
    """Tabelle COGS valide in un intervallo di date (una 'fetta' temporale)."""

    def __init__(self, sections: dict, default_cogs: float):
        self.default_cogs = float(sections.get("default_cogs", default_cogs) or default_cogs)
        self.exact_titles: dict[str, float] = {
            _norm_title(t): float(c)
            for t, c in (sections.get("exact_titles") or {}).items()
        }
        self.handle_to_cost: dict[str, float] = {}
        for sec in ("custom_products", "classic_rings", "bracelets"):
            for handle, cost in (sections.get(sec) or {}).items():
                self.handle_to_cost[str(handle).strip().lower()] = float(cost)
        self.title_rules: list[tuple[list[str], float]] = []
        for rule in (sections.get("title_rules") or []):
            kws = [str(k).strip().lower() for k in (rule.get("keywords") or [])]
            if kws and rule.get("cogs") is not None:
                self.title_rules.append((kws, float(rule["cogs"])))

    def match_title_rules(self, handle: Optional[str], title: Optional[str]) -> Optional[float]:
        tokens = set((_slugify(handle or "") + "-" + _slugify(title or "")).split("-"))
        tokens.discard("")
        for kws, cost in self.title_rules:
            if all(kw in tokens for kw in kws):
                return cost
        return None


class CogsResolver:
    """Risolve il COGS (USD) e la spedizione di un ordine, DATATI sulla data Rome dell'ordine."""

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

        # ---- COGS DATATO: periodo base (top-level) + overlay con effective_from ----
        self._base_period = _CogsPeriod(self._raw, self.default_cogs)
        self._cogs_periods: list[tuple[str, _CogsPeriod]] = []
        for entry in (self._raw.get("cogs_schedule") or []):
            eff = _to_date_str(entry.get("effective_from"))
            if not eff:
                continue
            merged = self._merge_sections(self._raw, entry)
            self._cogs_periods.append((eff, _CogsPeriod(merged, self.default_cogs)))
        self._cogs_periods.sort(key=lambda t: t[0])

        # Set di handle per famiglie note (classificazione unità prodotto): dalla BASE,
        # indipendenti dalla data (i prezzi cambiano, l'appartenenza alla famiglia no).
        self.classic_ring_handles: set[str] = {
            h.strip().lower() for h in (self._raw.get("classic_rings") or {})
        }
        self.bracelet_handles: set[str] = {
            h.strip().lower() for h in (self._raw.get("bracelets") or {})
        }

        # ---- SPEDIZIONE DATATA ----
        self._shipping_periods = self._build_shipping_periods()

    # -------------------------------------------------------------- caricamento
    def _load(self) -> dict:
        with open(self.yaml_path, "r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}

    @staticmethod
    def _merge_sections(base: dict, overlay: dict) -> dict:
        """Fonde un overlay dated sulla base: dict-merge per exact_titles/custom_products/
        classic_rings/bracelets (overlay vince); title_rules e default_cogs sostituiti se
        presenti nell'overlay. Il resto (collection_cogs ecc.) resta della base."""
        out = dict(base)
        for sec in ("exact_titles", "custom_products", "classic_rings", "bracelets"):
            merged = dict(base.get(sec) or {})
            merged.update(overlay.get(sec) or {})
            out[sec] = merged
        if overlay.get("title_rules") is not None:
            out["title_rules"] = overlay["title_rules"]
        if overlay.get("default_cogs") is not None:
            out["default_cogs"] = overlay["default_cogs"]
        return out

    def _period_for(self, order_date) -> _CogsPeriod:
        """Periodo COGS valido alla data: base se data assente o precedente al primo overlay."""
        d = _to_date_str(order_date)
        if not d:
            return self._base_period
        chosen = self._base_period
        for eff, period in self._cogs_periods:
            if eff <= d:
                chosen = period
            else:
                break
        return chosen

    # -------------------------------------------------------------- COGS
    def resolve_with_source(
        self, handle: Optional[str], title: Optional[str] = None, order_date=None
    ) -> tuple[float, str]:
        """
        Come cogs_for_handle ma ritorna anche QUALE layer ha deciso il costo:
        'exact-title' | 'handle' | 'title-slug' | 'family' | 'default'.

        Priorità (nel periodo in vigore alla data dell'ordine):
          1. TITOLO ESATTO (exact_titles)  2. handle esatto  3. titolo slugificato
          4. title_rules (famiglia)        5. default_cogs ($3)
        Costo 0 => default_cogs. Senza `order_date` usa i valori BASE (pre-primo-overlay).
        """
        period = self._period_for(order_date)

        if title:
            c = period.exact_titles.get(_norm_title(title))
            if c is not None and c != 0:
                return float(c), "exact-title"

        if handle:
            c = period.handle_to_cost.get(handle.strip().lower())
            if c is not None and c != 0:
                return float(c), "handle"

        if title:
            c = period.handle_to_cost.get(_slugify(title))
            if c is not None and c != 0:
                return float(c), "title-slug"

        c = period.match_title_rules(handle, title)
        if c is not None and c != 0:
            return float(c), "family"

        return period.default_cogs, "default"

    def cogs_for_handle(
        self, handle: Optional[str], title: Optional[str] = None, order_date=None
    ) -> float:
        """COGS in USD per un prodotto, DATATO sulla data Rome dell'ordine (vedi
        resolve_with_source). Senza `order_date` usa i valori BASE."""
        return self.resolve_with_source(handle, title, order_date)[0]

    # -------------------------------------------------------------- spedizione
    def _build_shipping_periods(self) -> list[tuple[str, dict]]:
        """Periodi di spedizione ordinati per data. Ogni periodo: {'flat': x} oppure
        {'formula': {...}}. Senza blocco `shipping`, un solo periodo flat = shipping_per_order."""
        raw = (self._raw.get("shipping") or {}).get("schedule")
        if not raw:
            return [("0001-01-01", {"flat": self.shipping_per_order})]
        periods: list[tuple[str, dict]] = []
        for entry in raw:
            eff = _to_date_str(entry.get("effective_from")) or "0001-01-01"
            if entry.get("flat_per_order") is not None:
                periods.append((eff, {"flat": float(entry["flat_per_order"])}))
            else:
                cb = {str(k).strip().upper(): float(v)
                      for k, v in (entry.get("country_base") or {}).items()}
                surch = {int(k): float(v)
                         for k, v in (entry.get("piece_surcharge") or {}).items()}
                excl = self._compile_excludes(entry.get("exclude_titles") or [])
                periods.append((eff, {"formula": {
                    "country_base": cb,
                    "default_country_base": float(entry.get("default_country_base", 6.5)),
                    "piece_surcharge": surch,
                    "overflow_from": int(entry.get("piece_surcharge_overflow_from", 6)),
                    "overflow_base": float(entry.get("piece_surcharge_overflow_base", 0.0)),
                    "overflow_step": float(entry.get("piece_surcharge_overflow_step", 0.0)),
                    "exclude": excl,
                }}))
        periods.sort(key=lambda t: t[0])
        return periods

    @staticmethod
    def _compile_excludes(phrases: list) -> list[tuple[str, bool]]:
        """(slug, is_single_token) per ogni frase da escludere dal conteggio pezzi."""
        out: list[tuple[str, bool]] = []
        for p in phrases:
            s = _slugify(str(p))
            if s:
                out.append((s, "-" not in s))
        return out

    def _shipping_period_for(self, order_date) -> dict:
        d = _to_date_str(order_date)
        if not self._shipping_periods:
            return {"flat": self.shipping_per_order}
        if not d:
            return self._shipping_periods[-1][1]
        chosen = self._shipping_periods[0][1]
        for eff, period in self._shipping_periods:
            if eff <= d:
                chosen = period
            else:
                break
        return chosen

    @staticmethod
    def _order_country(order: dict) -> str:
        for key in ("shipping_address", "billing_address"):
            addr = order.get(key) or {}
            cc = addr.get("country_code") or addr.get("country")
            if cc:
                return str(cc).strip().upper()
        return ""

    @staticmethod
    def _is_excluded(title: str, excludes: list[tuple[str, bool]]) -> bool:
        tslug = _slugify(title)
        if not tslug:
            return False
        tokens = set(tslug.split("-"))
        for spec, single in excludes:
            if single:
                if spec in tokens:
                    return True
            elif spec in tslug:
                return True
        return False

    def order_pieces(self, order: dict, order_date=None) -> int:
        """Pezzi FISICI spediti = Σ quantità dei line item NON esclusi (tip/gift card/
        protezione spedizione/digitale...). Box, organizer e size-adjuster CONTANO."""
        period = self._shipping_period_for(order_date)
        formula = period.get("formula")
        excludes = formula.get("exclude") if formula else []
        total = 0
        for li in order.get("line_items") or []:
            if self._is_excluded(li.get("title") or "", excludes or []):
                continue
            try:
                total += int(li.get("quantity") or 0)
            except (TypeError, ValueError):
                pass
        return total

    def _piece_surcharge(self, pieces: int, formula: dict) -> float:
        table = formula["piece_surcharge"]
        if pieces in table:
            return table[pieces]
        of = formula["overflow_from"]
        if pieces > of:
            return formula["overflow_base"] + formula["overflow_step"] * (pieces - of)
        return 0.0

    def shipping_for_order(self, order: dict, order_date=None) -> float:
        """
        Spedizione (USD) dell'ordine, DATATA:
          - periodo flat: `flat_per_order` per ordine (legacy $7, invariato);
          - periodo formula: country_base (paese di spedizione) + piece_surcharge (pezzi fisici);
            0 pezzi => $0.
        """
        period = self._shipping_period_for(order_date)
        if "flat" in period:
            return float(period["flat"])
        formula = period["formula"]
        pieces = self.order_pieces(order, order_date)
        if pieces <= 0:
            return 0.0
        cc = self._order_country(order)
        base = formula["country_base"].get(cc, formula["default_country_base"])
        return round(base + self._piece_surcharge(pieces, formula), 2)

    def shipping_caption(self) -> str:
        """Descrizione GENERATA dal config (come schedule_caption dei costi fissi): mai stale."""
        parts: list[str] = []
        for eff, period in self._shipping_periods:
            if "flat" in period:
                parts.append(f"flat ${period['flat']:,.2f}/order before "
                             f"{self._next_eff(eff)}")
            else:
                f = period["formula"]
                # raggruppa i paesi per importo base
                by_amt: dict[float, list[str]] = {}
                for cc, amt in f["country_base"].items():
                    by_amt.setdefault(amt, []).append(cc)
                grp = "; ".join(
                    f"{'/'.join(sorted(ccs))} ${amt:,.2f}"
                    for amt, ccs in sorted(by_amt.items())
                )
                of, ob, ostep = f["overflow_from"], f["overflow_base"], f["overflow_step"]
                sur = ", ".join(f"{p}→${v:,.2f}" for p, v in sorted(f["piece_surcharge"].items()))
                parts.append(
                    f"from {eff} per-order: country base ({grp}; other/missing "
                    f"${f['default_country_base']:,.2f}) + piece surcharge ({sur}, "
                    f">{of}→${ob:,.2f}+${ostep:,.2f}×(pieces−{of}))"
                )
        return "; ".join(parts) + "."

    def _next_eff(self, eff: str) -> str:
        """La effective_from del periodo successivo a `eff` (per la caption flat->formula)."""
        seen = False
        for e, _p in self._shipping_periods:
            if seen:
                return e
            if e == eff:
                seen = True
        return "the switch date"


@lru_cache(maxsize=1)
def get_resolver() -> CogsResolver:
    """Singleton del resolver (il file YAML si carica una sola volta)."""
    return CogsResolver()
