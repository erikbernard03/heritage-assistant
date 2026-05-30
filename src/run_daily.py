"""
Job notturno (cron 00:00 Europe/Rome): genera il report di ieri e lo invia su Telegram.

Esempio crontab (server in UTC; 00:00 Europe/Rome ~= 22:00/23:00 UTC a seconda
dell'ora legale — usare un cron tz-aware o impostare CRON_TZ):

    CRON_TZ=Europe/Rome
    0 0 * * *  cd /path/heritage-assistant && python -m src.run_daily >> logs/daily.log 2>&1
"""
from __future__ import annotations

import asyncio

from telegram import Bot
from telegram.constants import ParseMode

from config import settings
from src.report import build_daily_report


async def _send(text: str) -> None:
    bot = Bot(settings.TELEGRAM_BOT_TOKEN)
    await bot.send_message(
        chat_id=settings.TELEGRAM_CHAT_ID,
        text=text,
        parse_mode=ParseMode.MARKDOWN,
    )


def main() -> None:
    _, text = build_daily_report(persist=True)
    asyncio.run(_send(text))
    print("[run_daily] report inviato.")


if __name__ == "__main__":
    main()
