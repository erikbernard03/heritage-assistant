"""
Finestre temporali per la dashboard (codice PURO e deterministico — niente rete).

Ogni funzione ritorna una tupla (start_iso, end_iso) di date ISO 'YYYY-MM-DD'
INCLUSIVE, pronte per store.get_daily_metrics_range(start, end).

Tutte le date sono nel fuso Europe/Rome: la "giornata" del business coincide con
quella dei report notturni. `today` è iniettabile per i test (default: oggi a Roma).

Regole:
- "This week": settimana con inizio LUNEDÌ (ISO), dal lunedì a `today` (month-to-date-like).
- "This month": dal 1° del mese corrente a `today`.
- "Last month": mese solare precedente COMPLETO (1° → ultimo giorno).
- "Last 7 days": finestra scorrevole di 7 giorni che include oggi (today-6 → today).
- "All time": da una data molto anteriore a `today` (copre tutto lo storico).
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

import pytz

from config import settings

# Data d'inizio "All time": abbondantemente prima di qualsiasi dato reale.
_EPOCH = date(2000, 1, 1)


def rome_today(today: Optional[date] = None) -> date:
    """Data odierna nel fuso Europe/Rome (o `today` iniettata per i test)."""
    if today is not None:
        return today
    from datetime import datetime

    return datetime.now(pytz.timezone(settings.TIMEZONE)).date()


def last_7_days(today: Optional[date] = None) -> tuple[str, str]:
    """Ultimi 7 giorni inclusi oggi: [today-6, today]."""
    t = rome_today(today)
    return (t - timedelta(days=6)).isoformat(), t.isoformat()


def this_week(today: Optional[date] = None) -> tuple[str, str]:
    """Settimana corrente con inizio LUNEDÌ: [lunedì, today]."""
    t = rome_today(today)
    monday = t - timedelta(days=t.weekday())   # weekday(): lun=0 ... dom=6
    return monday.isoformat(), t.isoformat()


def this_month(today: Optional[date] = None) -> tuple[str, str]:
    """Mese corrente (month-to-date): [1° del mese, today]."""
    t = rome_today(today)
    return t.replace(day=1).isoformat(), t.isoformat()


def last_month(today: Optional[date] = None) -> tuple[str, str]:
    """Mese solare PRECEDENTE completo: [1° del mese scorso, ultimo giorno del mese scorso]."""
    t = rome_today(today)
    first_this = t.replace(day=1)
    last_prev = first_this - timedelta(days=1)          # ultimo giorno del mese scorso
    first_prev = last_prev.replace(day=1)               # 1° del mese scorso
    return first_prev.isoformat(), last_prev.isoformat()


def all_time(today: Optional[date] = None) -> tuple[str, str]:
    """Tutto lo storico: [2000-01-01, today]."""
    t = rome_today(today)
    return _EPOCH.isoformat(), t.isoformat()


def custom_range(start: date, end: date) -> tuple[str, str]:
    """Intervallo personalizzato: ordina gli estremi e li ritorna inclusivi."""
    if end < start:
        start, end = end, start
    return start.isoformat(), end.isoformat()


# Etichette -> funzione (per il selettore UI). Custom è gestito a parte (date picker).
PRESETS = {
    "Last 7 days": last_7_days,
    "This week": this_week,
    "This month": this_month,
    "Last month": last_month,
    "All time": all_time,
}
