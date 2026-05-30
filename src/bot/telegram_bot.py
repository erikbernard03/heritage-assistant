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
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from config import settings
from src.report import build_daily_report, build_monthly_pl

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
        "Heritage Ring · AI Assistant (Phase 1 — Shopify).\n\n"
        "Commands:\n"
        "• /report — yesterday's Shopify report\n"
        "• /pl YEAR MONTH — monthly P&L (e.g. /pl 2026 4)\n\n"
        "Or just ask me a question (e.g. \"how did yesterday go?\")."
    )


async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        await update.message.reply_text("⛔️ Unauthorized chat.")
        return

    msg = await update.message.reply_text("⏳ Generating yesterday's Shopify report…")
    try:
        # build_daily_report fa I/O di rete bloccante: lo eseguo in un thread
        _, text = await asyncio.to_thread(build_daily_report)
        await msg.edit_text(text, parse_mode=ParseMode.MARKDOWN)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Errore nella generazione del report")
        await msg.edit_text(f"❌ Report error: {exc}")


async def cmd_pl(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/pl ANNO MESE -> P&L mensile deterministico dal database."""
    if not _authorized(update):
        await update.message.reply_text("⛔️ Unauthorized chat.")
        return
    try:
        year = int(context.args[0])
        month = int(context.args[1])
        if not (1 <= month <= 12):
            raise ValueError
    except (IndexError, ValueError):
        await update.message.reply_text("Usage: /pl YEAR MONTH — e.g. /pl 2026 4")
        return

    msg = await update.message.reply_text(f"⏳ Computing the {year}-{month:02d} P&L…")
    try:
        text = await asyncio.to_thread(build_monthly_pl, year, month)
        await msg.edit_text(text, parse_mode=ParseMode.MARKDOWN)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Errore nel P&L mensile")
        await msg.edit_text(f"❌ P&L error: {exc}")


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Messaggi liberi -> risposta AI (Claude legge i dati dal DB)."""
    if not _authorized(update):
        return
    question = (update.message.text or "").strip()
    if not question:
        return

    await update.message.chat.send_action(ChatAction.TYPING)
    try:
        from src.ai.assistant import answer_question

        answer = await asyncio.to_thread(answer_question, question)
        await update.message.reply_text(answer, parse_mode=ParseMode.MARKDOWN)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Errore nella risposta libera")
        await update.message.reply_text(f"❌ Error: {exc}")


def build_application() -> Application:
    if not settings.TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN non configurato (.env).")
    app = Application.builder().token(settings.TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("report", cmd_report))
    app.add_handler(CommandHandler("pl", cmd_pl))
    # qualsiasi testo non-comando -> assistente AI
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    return app


def main() -> None:
    app = build_application()
    logger.info("Bot avviato. In ascolto dei comandi (/report).")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
