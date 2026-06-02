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


async def _send_chunks(message, text: str) -> None:
    """Invia testo lungo in più messaggi (limite Telegram ~4096 char)."""
    chunk = 3800
    for i in range(0, len(text), chunk):
        await message.reply_text(text[i : i + chunk])


async def cmd_klaviyo_check(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/klaviyo_check (admin) -> esegue la diagnostica Klaviyo e la invia in chat."""
    if not _authorized(update):
        await update.message.reply_text("⛔️ Unauthorized chat.")
        return

    msg = await update.message.reply_text("⏳ Running Klaviyo diagnostic…")
    try:
        from src.diagnostics import klaviyo_diagnostic

        text = await asyncio.to_thread(klaviyo_diagnostic)
        # niente parse_mode: il testo può contenere caratteri speciali (nomi campagne)
        await msg.edit_text(text[:3800])
        if len(text) > 3800:
            await _send_chunks(update.message, text[3800:])
    except Exception as exc:  # noqa: BLE001
        logger.exception("Errore nella diagnostica Klaviyo")
        await msg.edit_text(f"❌ Klaviyo diagnostic error: {exc}")


async def cmd_meta_check(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/meta_check (admin) -> diagnostica live Meta (insights) e la invia in chat."""
    if not _authorized(update):
        await update.message.reply_text("⛔️ Unauthorized chat.")
        return

    msg = await update.message.reply_text("⏳ Running Meta diagnostic…")
    try:
        from src.diagnostics import meta_diagnostic

        text = await asyncio.to_thread(meta_diagnostic)
        # niente parse_mode: il testo può contenere caratteri speciali (nomi campagne/errori)
        await msg.edit_text(text[:3800])
        if len(text) > 3800:
            await _send_chunks(update.message, text[3800:])
    except Exception as exc:  # noqa: BLE001
        logger.exception("Errore nella diagnostica Meta")
        await msg.edit_text(f"❌ Meta diagnostic error: {exc}")


async def cmd_tw_check(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/tw_check (admin) -> diagnostica live Triple Whale (TikTok) e la invia in chat."""
    if not _authorized(update):
        await update.message.reply_text("⛔️ Unauthorized chat.")
        return

    msg = await update.message.reply_text("⏳ Running Triple Whale (TikTok) diagnostic…")
    try:
        from src.diagnostics import triplewhale_diagnostic

        text = await asyncio.to_thread(triplewhale_diagnostic)
        await msg.edit_text(text[:3800])
        if len(text) > 3800:
            await _send_chunks(update.message, text[3800:])
    except Exception as exc:  # noqa: BLE001
        logger.exception("Errore nella diagnostica Triple Whale")
        await msg.edit_text(f"❌ Triple Whale diagnostic error: {exc}")


async def cmd_google_check(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/google_check (admin) -> diagnostica live Google (via Triple Whale) in chat."""
    if not _authorized(update):
        await update.message.reply_text("⛔️ Unauthorized chat.")
        return

    msg = await update.message.reply_text("⏳ Running Google Ads diagnostic…")
    try:
        from src.diagnostics import google_diagnostic

        text = await asyncio.to_thread(google_diagnostic)
        await msg.edit_text(text[:3800])
        if len(text) > 3800:
            await _send_chunks(update.message, text[3800:])
    except Exception as exc:  # noqa: BLE001
        logger.exception("Errore nella diagnostica Google")
        await msg.edit_text(f"❌ Google diagnostic error: {exc}")


async def cmd_refresh_today(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/refresh_today (admin) -> force re-pull di tutte le piattaforme (oggi + ieri),
    sovrascrive le righe DB e reinvia il report aggiornato di ieri."""
    if not _authorized(update):
        await update.message.reply_text("⛔️ Unauthorized chat.")
        return

    msg = await update.message.reply_text(
        "⏳ Force-refreshing all platforms for today & yesterday (overwriting DB)…"
    )
    try:
        from src.report import refresh_today_and_yesterday

        text = await asyncio.to_thread(refresh_today_and_yesterday)
        try:
            await msg.edit_text(text, parse_mode=ParseMode.MARKDOWN)
        except Exception:  # noqa: BLE001 — troppo lungo o markdown non valido
            await msg.edit_text(text[:3800])
            if len(text) > 3800:
                await _send_chunks(update.message, text[3800:])
    except Exception as exc:  # noqa: BLE001
        logger.exception("Errore nel refresh_today")
        await msg.edit_text(f"❌ Refresh error: {exc}")


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
    app.add_handler(CommandHandler("klaviyo_check", cmd_klaviyo_check))
    app.add_handler(CommandHandler("meta_check", cmd_meta_check))
    app.add_handler(CommandHandler("tw_check", cmd_tw_check))
    app.add_handler(CommandHandler("google_check", cmd_google_check))
    app.add_handler(CommandHandler("refresh_today", cmd_refresh_today))
    # qualsiasi testo non-comando -> assistente AI
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    return app


def main() -> None:
    app = build_application()
    logger.info("Bot avviato. In ascolto dei comandi (/report).")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
