"""
Aggregazioni PURE per la dashboard (mensile, visitatori, serie break-even, unità prodotto).
Nessuna rete: funzioni deterministiche testabili. La parte con accesso DB sta in app.py.

Regole coerenti con /report7: totali di periodo (mai media dei tassi giornalieri).
"""
from __future__ import annotations

import calendar
from datetime import date
from typing import Optional

from src.metrics.profit import compute_breakeven

# Mesi PRIMA di questo: se parziali (copertura incompleta) vengono NASCOSTI del tutto.
# Dal mese incluso in poi, un mese parziale resta visibile ma marcato "(partial)".
_HIDE_PARTIAL_BEFORE = "2026-06"


def month_of(day_iso: str) -> str:
    """Mese di calendario di un giorno ISO 'YYYY-MM-DD' -> 'YYYY-MM'."""
    return str(day_iso)[:7]


_MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


def month_label(month_key: str) -> str:
    """Etichetta leggibile di un mese 'YYYY-MM' -> es. "July '26". Fallback: la chiave."""
    try:
        y, m = month_key.split("-")
        return f"{_MONTH_NAMES[int(m) - 1]} '{y[-2:]}"
    except (ValueError, IndexError):
        return month_key


def group_by_month(daily_rows: list[dict]) -> dict[str, list[dict]]:
    """Raggruppa le righe daily_metrics per mese di calendario (YYYY-MM), ordinate per giorno."""
    by: dict[str, list[dict]] = {}
    for r in daily_rows:
        by.setdefault(month_of(r["day"]), []).append(r)
    return {k: sorted(v, key=lambda x: x["day"]) for k, v in sorted(by.items())}


def monthly_visitors(month_rows: list[dict]) -> tuple[float, bool]:
    """
    Visitatori del mese. Ritorna (visitatori, is_estimated).

    Reali: se OGNI giorno del mese ha store_sessions valorizzato (scope read_reports OK),
    somma le sessioni reali. Altrimenti STIMA = Σ(ordini ÷ store CVR) sui giorni con CVR>0,
    etichettata "est.".
    """
    if month_rows and all(r.get("store_sessions") is not None for r in month_rows):
        return float(sum(int(r["store_sessions"]) for r in month_rows)), False
    est = 0.0
    for r in month_rows:
        cvr = float(r.get("store_cvr") or 0)
        orders = float(r.get("num_orders") or 0)
        if cvr > 0 and orders > 0:
            est += orders / cvr
    return est, True


def period_store_cvr(month_rows: list[dict]) -> float:
    """
    Store CVR di periodo con il metodo TOTALI (conversioni/sessioni), MAI media dei tassi.
    Sessioni ricostruite per giorno = ordini/cvr (come aggregate_week). Frazione.
    """
    tot_conv = 0.0
    tot_sessions = 0.0
    for r in month_rows:
        cvr = float(r.get("store_cvr") or 0)
        orders = float(r.get("num_orders") or 0)
        if cvr > 0 and orders > 0:
            tot_conv += orders
            tot_sessions += orders / cvr
    return (tot_conv / tot_sessions) if tot_sessions > 0 else 0.0


def monthly_store_cvr(month_rows: list[dict]) -> Optional[float]:
    """
    Store CVR MENSILE = Σ ordini ÷ Σ sessioni del mese (metodo TOTALI, mai media dei tassi).

    Usa le SESSIONI REALI (colonna store_sessions, popolata dal backfill) quando presenti.
    Se nessun giorno ha sessioni reali, ripiega sulla ricostruzione da store_cvr (giorni
    cron). Se nessuna delle due è disponibile -> None ("n/a", NON 0.00%).

    Bug precedente: la vista mensile leggeva m.store_cvr (ricostruito dal campo store_cvr,
    che il backfill non ha impostato) -> 0.00% per ogni mese pur avendo le sessioni reali.
    """
    tot_orders = sum(int(r.get("num_orders") or 0) for r in month_rows)
    tot_sessions = sum(
        int(r["store_sessions"]) for r in month_rows if r.get("store_sessions") is not None
    )
    if tot_sessions > 0:
        return tot_orders / tot_sessions
    fallback = period_store_cvr(month_rows)   # giorni con store_cvr ma senza sessions
    return fallback if fallback > 0 else None


def daily_breakeven_series(
    rows_with_lookback: list[dict], period_days: set[str]
) -> list[dict]:
    """
    Serie giornaliera per i grafici del periodo: per ogni giorno D in `period_days`,
    calcola AOV del giorno e il break-even (ROAS, CPA) dalla STESSA formula del report,
    cioè dai 4 giorni REALI più recenti PRIMA di D (come report._load_breakeven).

    `rows_with_lookback` deve includere giorni PRIMA dell'inizio periodo (lookback), così
    anche i primi giorni hanno i loro 4 giorni precedenti. Ritorna una lista ordinata di
    {day, aov, be_roas, be_cpa} (be_* possono essere None se non calcolabili).
    """
    rows = sorted(rows_with_lookback, key=lambda r: r["day"])
    out: list[dict] = []
    for r in rows:
        d = r["day"]
        if d not in period_days:
            continue
        orders = float(r.get("num_orders") or 0)
        revenue = float(r.get("revenue") or 0)
        aov = (revenue / orders) if orders > 0 else None
        priors = [x for x in rows if x["day"] < d][-4:]   # 4 giorni reali precedenti
        be_roas, be_cpa = compute_breakeven(priors)
        out.append({"day": d, "aov": aov, "be_roas": be_roas, "be_cpa": be_cpa})
    return out


def month_expected_days(month_key: str, today: date) -> int:
    """Giorni ATTESI con dati per un mese: mese corrente -> fino a oggi; passato -> mese intero."""
    y, mo = (int(x) for x in month_key.split("-"))
    if (y, mo) == (today.year, today.month):
        return today.day
    return calendar.monthrange(y, mo)[1]


def month_is_partial(month_key: str, month_rows: list[dict], today: date) -> bool:
    """True se il mese ha MENO giorni con dati di quelli attesi (copertura incompleta)."""
    have = len({r["day"] for r in month_rows})
    return have < month_expected_days(month_key, today)


def filter_visible_months(
    by_month: dict[str, list[dict]], today: date
) -> list[tuple[str, list[dict], bool]]:
    """
    Applica la regola sui mesi parziali. Ritorna [(month_key, rows, is_partial), ...]:
    - mese COMPLETO -> incluso (is_partial=False)
    - mese PARZIALE prima di _HIDE_PARTIAL_BEFORE -> NASCOSTO (escluso)
    - mese PARZIALE da _HIDE_PARTIAL_BEFORE in poi -> incluso e marcato (is_partial=True)
    """
    out: list[tuple[str, list[dict], bool]] = []
    for month_key in sorted(by_month):
        rows = by_month[month_key]
        partial = month_is_partial(month_key, rows, today)
        if partial and month_key < _HIDE_PARTIAL_BEFORE:
            continue  # nascondi i mesi parziali storici (dati fuorvianti)
        out.append((month_key, rows, partial))
    return out


def units_by_month(product_units_rows: list[dict]) -> dict[str, dict[str, int]]:
    """
    Somma le unità per (mese, product_key) da product_units_daily.
    Ritorna {mese: {product_key: units}} con i mesi ordinati.
    """
    by: dict[str, dict[str, int]] = {}
    for r in product_units_rows:
        month = month_of(r["day"])
        key = r.get("product_key") or "other"
        try:
            units = int(r.get("units") or 0)
        except (TypeError, ValueError):
            units = 0
        by.setdefault(month, {})
        by[month][key] = by[month].get(key, 0) + units
    return {k: by[k] for k in sorted(by)}


def _margin_pct(numer: float, denom: float) -> Optional[float]:
    return (numer / denom * 100.0) if denom else None
