"""
Job notturno (ONE-SHOT): genera il report di IERI, lo invia su Telegram, ed ESCE.

Deve terminare in pochi secondi. NON avvia il bot in polling né alcun processo
long-running: è uno script one-shot che fa il suo lavoro e fa `sys.exit(0)`.

Invio Telegram via HTTP sincrono (requests): niente event loop asyncio e niente
client async di python-telegram-bot, che lasciavano risorse aperte facendo
"appendere" il processo (rimaneva in stato Running all'infinito su Railway).

Schedulazione su Railway (Cron Schedule in UTC, niente ora legale):

    Cron Schedule (UTC):  0 22,23 * * *
    Variabile:            RAILWAY_CRON_GUARD=1

La guardia fa procedere SOLO la run che cade alle 00:xx di Europe/Rome:
- estate (CEST, UTC+2): 22:00 UTC = 00:00 Roma -> procede; 23:00 UTC = 01:00 -> salta
- inverno (CET,  UTC+1): 22:00 UTC = 23:00 Roma -> salta;  23:00 UTC = 00:00 -> procede
=> esattamente un report al giorno, sempre a mezzanotte di Roma, tutto l'anno.
(Le esecuzioni che parti vedi "alle 00:02 / 01:02" sono proprio gli orari di Roma
delle due run UTC, con il piccolo ritardo di schedulazione di Railway.)
"""
from __future__ import annotations

import os
import sys
from datetime import datetime

import pytz
import requests

from config import settings
from src.report import build_daily_report

_SEND_TIMEOUT = 30  # secondi per ogni chiamata Telegram


def _should_run_now() -> bool:
    """True se la guardia è disattiva, o se a Europe/Rome è l'ora 00:xx."""
    if os.getenv("RAILWAY_CRON_GUARD", "").strip().lower() not in ("1", "true", "yes"):
        return True
    rome_hour = datetime.now(pytz.timezone(settings.TIMEZONE)).hour
    return rome_hour == 0


def _send(text: str) -> None:
    """Invia il report via HTTP sincrono (chunked per il limite 4096; fallback no-markdown)."""
    url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage"
    chunk = 3900
    for i in range(0, len(text), chunk):
        part = text[i : i + chunk]
        resp = requests.post(
            url,
            json={"chat_id": settings.TELEGRAM_CHAT_ID, "text": part, "parse_mode": "Markdown"},
            timeout=_SEND_TIMEOUT,
        )
        if not resp.ok:
            # es. errore di parsing Markdown -> reinvia come testo semplice
            requests.post(
                url,
                json={"chat_id": settings.TELEGRAM_CHAT_ID, "text": part},
                timeout=_SEND_TIMEOUT,
            )


def main() -> int:
    """Esegue una volta sola e ritorna un exit code (0 = ok)."""
    if not _should_run_now():
        rome_now = datetime.now(pytz.timezone(settings.TIMEZONE)).strftime("%H:%M")
        print(f"[run_daily] not midnight in Rome (now {rome_now}): skipping.")
        return 0

    _, text = build_daily_report(persist=True)
    _send(text)
    print("[run_daily] report sent.")
    return 0


if __name__ == "__main__":
    # sys.exit garantisce la terminazione netta del processo (niente hang).
    sys.exit(main())
