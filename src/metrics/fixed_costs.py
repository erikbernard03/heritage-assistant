"""
Costi fissi mensili DATATI: ogni giorno usa il valore in vigore a quella data
(settings.FIXED_COSTS_SCHEDULE). Codice puro/deterministico.
"""
from __future__ import annotations

from config import settings


def monthly_fixed_cost_for(day_iso: str) -> float:
    """Costo fisso MENSILE (USD) in vigore al giorno `day_iso` ('YYYY-MM-DD')."""
    sched = sorted(
        (settings.FIXED_COSTS_SCHEDULE or []), key=lambda e: str(e.get("from", ""))
    )
    if not sched:
        return float(settings.COSTI_FISSI_MENSILI)
    amount = float(sched[0].get("monthly", settings.COSTI_FISSI_MENSILI))
    for entry in sched:
        if str(entry.get("from", "")) <= day_iso:
            amount = float(entry.get("monthly", amount))
        else:
            break
    return amount


def daily_fixed_allocation(day_iso: str) -> float:
    """Quota GIORNALIERA dei costi fissi al giorno `day_iso` = mensile_in_vigore / 30."""
    days = settings.GIORNI_MESE_ALLOCAZIONE or 30
    return monthly_fixed_cost_for(day_iso) / days


def _fmt_money(v: float) -> str:
    """$ con separatore migliaia; 2 decimali solo se non intero."""
    return f"${v:,.0f}" if float(v).is_integer() else f"${v:,.2f}"


def schedule_caption() -> str:
    """
    Descrizione GENERATA da settings.FIXED_COSTS_SCHEDULE (mai hardcoded -> non diventa stale).
    Es.: "$5,668 → $7,666 from 2026-06-11 → $6,117 from 2026-08-02 → $12,135.77 from 2026-09-01,
    ÷30 per day." Il primo valore è la baseline; i successivi mostrano la data di entrata.
    """
    days = settings.GIORNI_MESE_ALLOCAZIONE or 30
    sched = sorted((settings.FIXED_COSTS_SCHEDULE or []), key=lambda e: str(e.get("from", "")))
    if not sched:
        return f"{_fmt_money(float(settings.COSTI_FISSI_MENSILI))}/month, ÷{days} per day."
    parts = [_fmt_money(float(sched[0].get("monthly", settings.COSTI_FISSI_MENSILI)))]
    for entry in sched[1:]:
        parts.append(f"{_fmt_money(float(entry.get('monthly', 0)))} from {entry.get('from')}")
    return " → ".join(parts) + f", ÷{days} per day."
