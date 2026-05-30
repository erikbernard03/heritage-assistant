"""
Job notturno: genera il report di IERI e lo invia su Telegram.

Su Railway il Cron Schedule è in UTC e non gestisce l'ora legale. Per far partire
il report sempre alla mezzanotte di Europe/Rome (CET=UTC+1 / CEST=UTC+2):

    Cron Schedule (UTC):  0 22,23 * * *
    Variabile:            RAILWAY_CRON_GUARD=1

Con la guardia attiva, il job procede SOLO se a Roma sono le 00:xx, altrimenti
esce senza fare nulla. Così esattamente una delle due esecuzioni (22:00 o 23:00 UTC)
coincide con la mezzanotte di Roma in qualsiasi periodo dell'anno.

In locale (senza RAILWAY_CRON_GUARD) il job gira sempre: utile per i test manuali.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime

import pytz
from telegram import Bot
from telegram.constants import ParseMode

from config import settings
from src.report import build_daily_report


def _should_run_now() -> bool:
    """True se la guardia è disattiva, o se a Europe/Rome è l'ora 00:xx."""
    if os.getenv("RAILWAY_CRON_GUARD", "").strip() not in ("1", "true", "yes"):
        return True
    rome_hour = datetime.now(pytz.timezone(settings.TIMEZONE)).hour
    return rome_hour == 0


async def _send(text: str) -> None:
    bot = Bot(settings.TELEGRAM_BOT_TOKEN)
    await bot.send_message(
        chat_id=settings.TELEGRAM_CHAT_ID,
        text=text,
        parse_mode=ParseMode.MARKDOWN,
    )


def main() -> None:
    if not _should_run_now():
        rome_now = datetime.now(pytz.timezone(settings.TIMEZONE)).strftime("%H:%M")
        print(f"[run_daily] non è mezzanotte a Roma (ora {rome_now}): salto.")
        return
    _, text = build_daily_report(persist=True)
    asyncio.run(_send(text))
    print("[run_daily] report inviato.")


if __name__ == "__main__":
    main()
