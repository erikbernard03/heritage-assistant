"""
Job notturno (ONE-SHOT): genera il report di IERI, lo invia su Telegram, ed ESCE.

Deve terminare in pochi secondi. NON avvia il bot in polling né alcun processo
long-running: è uno script one-shot che fa il suo lavoro e termina.

GARANZIE DI TERMINAZIONE (anti-hang):
- Invio Telegram via HTTP sincrono (requests): niente event loop asyncio / client
  async di python-telegram-bot.
- Generazione report a prova di guasto: se un connettore fallisce/va in timeout, la
  sua parte viene saltata (0/empty) e il report parte comunque (vedi src/report.py).
- WATCHDOG: un thread daemon forza l'uscita (os._exit) se l'intera run supera ~3 min.
- USCITA HARD con os._exit(): termina subito il processo senza attendere thread o
  connection pool ancora vivi (es. il thread di refresh token di Supabase), che
  altrimenti terrebbero il processo in stato Running per ore.

Schedulazione su Railway (Cron Schedule in UTC, niente ora legale):

    Cron Schedule (UTC):  0 22,23 * * *
    Variabile (opzionale): RAILWAY_CRON_GUARD  (default = guardia ATTIVA)

La guardia mezzanotte-Roma è ATTIVA DI DEFAULT (anche senza env var). Per disattivarla
(test/refresh immediato) imposta RAILWAY_CRON_GUARD=0.
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
import threading
import time as _time
from datetime import datetime

import pytz
import requests

from config import settings
from src.report import build_daily_report

_SEND_TIMEOUT = 30  # secondi per ogni chiamata Telegram
# Tetto massimo per l'intera run; oltre, il watchdog forza l'uscita.
WATCHDOG_SECONDS = int(os.getenv("RUN_DAILY_WATCHDOG_SECONDS", "180"))


def _start_watchdog(seconds: int) -> None:
    """Thread daemon: se la run supera `seconds`, forza l'uscita immediata."""
    def _kill() -> None:
        _time.sleep(seconds)
        print(f"[run_daily] WATCHDOG: run exceeded {seconds}s — forcing exit.", flush=True)
        os._exit(2)

    threading.Thread(target=_kill, daemon=True, name="run-daily-watchdog").start()


def _should_run_now() -> tuple[bool, int]:
    """
    (deve_inviare, ora_Roma).

    La guardia mezzanotte-Roma è ATTIVA DI DEFAULT (anche se la env var non è impostata):
    invia SOLO se a Europe/Rome è l'ora 00:xx. Così, con cron UTC `0 22,23 * * *`, una
    sola delle due run cade a mezzanotte di Roma (DST-safe) e invia; l'altra esce.
    Per disabilitare la guardia (es. test/refresh immediato): RAILWAY_CRON_GUARD=0.
    """
    rome_hour = datetime.now(pytz.timezone(settings.TIMEZONE)).hour
    guard = os.getenv("RAILWAY_CRON_GUARD", "1").strip().lower()
    if guard in ("0", "false", "no", "off", "disabled"):
        return True, rome_hour       # guardia disabilitata -> esegue/invia sempre
    return rome_hour == 0, rome_hour


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
    should_send, rome_hour = _should_run_now()
    rome_now = datetime.now(pytz.timezone(settings.TIMEZONE)).strftime("%Y-%m-%d %H:%M")
    guard_env = os.getenv("RAILWAY_CRON_GUARD", "(unset→guard ON)")
    print(
        f"[run_daily] Rome now {rome_now} (hour {rome_hour}) · "
        f"RAILWAY_CRON_GUARD={guard_env} · decision="
        f"{'SEND (midnight Rome)' if should_send else 'SKIP (not midnight Rome)'}",
        flush=True,
    )
    if not should_send:
        return 0

    # Log esplicito del giorno trattato: così nei log di Railway si vede CHE notte
    # è girato il cron (e, con il log di _persist, se il giorno è stato salvato).
    from src.report import yesterday_window

    target = yesterday_window().day_str
    print(f"[run_daily] RUN START — report day={target}", flush=True)

    # Generazione: se fallisce del tutto, invio comunque una nota di errore.
    try:
        _, text = build_daily_report(persist=True)
    except Exception as exc:  # noqa: BLE001
        print(f"[run_daily] report generation FAILED for day={target}: {exc}", flush=True)
        text = f"⚠️ Daily report could not be generated: {exc}"

    try:
        _send(text)
        print("[run_daily] report sent.", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"[run_daily] send FAILED: {exc}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    _start_watchdog(WATCHDOG_SECONDS)
    code = 0
    try:
        code = main() or 0
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 0
    except Exception as exc:  # noqa: BLE001
        print(f"[run_daily] fatal: {exc}", flush=True)
        code = 1
    sys.stdout.flush()
    sys.stderr.flush()
    # USCITA HARD: termina subito, ignorando thread/connection pool ancora vivi.
    os._exit(code)
