"""
Aggregazioni PURE per la dashboard (mensile, visitatori, serie break-even, unità prodotto).
Nessuna rete: funzioni deterministiche testabili. La parte con accesso DB sta in app.py.

Regole coerenti con /report7: totali di periodo (mai media dei tassi giornalieri).
"""
from __future__ import annotations

from typing import Optional

from src.metrics.profit import compute_breakeven


def month_of(day_iso: str) -> str:
    """Mese di calendario di un giorno ISO 'YYYY-MM-DD' -> 'YYYY-MM'."""
    return str(day_iso)[:7]


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
