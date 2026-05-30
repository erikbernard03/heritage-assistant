"""
Bot Telegram — Fase 1 (solo Shopify).

Comando disponibile:
  /report  -> genera al volo il report di IERI da Shopify (ordini, revenue,
              net profit operativo e netto) e lo invia in chat.

I comandi /pl e le domande libere (via Claude) arriveranno nelle fasi successive.
Nota: il calcolo dei numeri è 100% deterministico; nessun LLM è coinvolto.
"""
from __future__ import annotations

import asyncio
import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

from config import settings
from src.report import build_daily_report

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("heritage.bot")


def _authorized(update: Update) -> bool:
    """Se TELEGRAM_CHAT_ID è impostato, accetta solo quella chat."""
    if not settings.TELEGRAM_CHAT_ID:
        return True
    chat = update.effective_chat
    return bool(chat and str(chat.id) == str(settings.TELEGRAM_CHAT_ID))


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Heritage Ring · Assistente AI (Fase 1 — Shopify).\n"
        "Scrivi /report per il report di ieri."
    )


async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        await update.message.reply_text("⛔️ Chat non autorizzata.")
        return

    msg = await update.message.reply_text("⏳ Genero il report Shopify di ieri…")
    try:
        # build_daily_report fa I/O di rete bloccante: lo eseguo in un thread
        _, text = await asyncio.to_thread(build_daily_report)
        await msg.edit_text(text, parse_mode=ParseMode.MARKDOWN)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Errore nella generazione del report")
        await msg.edit_text(f"❌ Errore nel report: {exc}")


def build_application() -> Application:
    if not settings.TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN non configurato (.env).")
    app = Application.builder().token(settings.TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("report", cmd_report))
    return app


def main() -> None:
    app = build_application()
    logger.info("Bot avviato. In ascolto dei comandi (/report).")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
