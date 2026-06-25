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
from src.report import (
    build_daily_report,
    build_month_report,
    build_monthly_pl,
    build_weekly_report,
)

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
        "• /report7 — aggregated last-7-days report\n"
        "• /report5 — aggregated last-5-days report\n"
        "• /reportmonth — month-to-date report (current calendar month)\n"
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


async def cmd_report7(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/report7 -> report aggregato sugli ultimi 7 giorni con dati (stesso layout)."""
    if not _authorized(update):
        await update.message.reply_text("⛔️ Unauthorized chat.")
        return
    msg = await update.message.reply_text("⏳ Building the 7-day report…")
    try:
        text = await asyncio.to_thread(build_weekly_report)
        try:
            await msg.edit_text(text, parse_mode=ParseMode.MARKDOWN)
        except Exception:  # noqa: BLE001 — troppo lungo / markdown
            await msg.edit_text(text[:3800])
            if len(text) > 3800:
                await _send_chunks(update.message, text[3800:])
    except Exception as exc:  # noqa: BLE001
        logger.exception("Errore nel report 7 giorni")
        await msg.edit_text(f"❌ 7-day report error: {exc}")


async def cmd_report5(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/report5 -> report aggregato sugli ultimi 5 giorni con dati (stesso layout)."""
    if not _authorized(update):
        await update.message.reply_text("⛔️ Unauthorized chat.")
        return
    msg = await update.message.reply_text("⏳ Building the 5-day report…")
    try:
        text = await asyncio.to_thread(build_weekly_report, 5)
        try:
            await msg.edit_text(text, parse_mode=ParseMode.MARKDOWN)
        except Exception:  # noqa: BLE001
            await msg.edit_text(text[:3800])
            if len(text) > 3800:
                await _send_chunks(update.message, text[3800:])
    except Exception as exc:  # noqa: BLE001
        logger.exception("Errore nel report 5 giorni")
        await msg.edit_text(f"❌ 5-day report error: {exc}")


async def cmd_reportmonth(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/reportmonth -> report aggregato del mese CORRENTE (month-to-date, stesso layout)."""
    if not _authorized(update):
        await update.message.reply_text("⛔️ Unauthorized chat.")
        return
    msg = await update.message.reply_text("⏳ Building the month-to-date report…")
    try:
        text = await asyncio.to_thread(build_month_report)
        try:
            await msg.edit_text(text, parse_mode=ParseMode.MARKDOWN)
        except Exception:  # noqa: BLE001 — troppo lungo / markdown
            await msg.edit_text(text[:3800])
            if len(text) > 3800:
                await _send_chunks(update.message, text[3800:])
    except Exception as exc:  # noqa: BLE001
        logger.exception("Errore nel report mensile (month-to-date)")
        await msg.edit_text(f"❌ Month-to-date report error: {exc}")


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


async def cmd_backfill(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/backfill START END (admin) -> re-pull Shopify e riempie daily_metrics nel range."""
    if not _authorized(update):
        await update.message.reply_text("⛔️ Unauthorized chat.")
        return
    args = context.args or []
    start = args[0] if len(args) >= 1 else None
    end = args[1] if len(args) >= 2 else start
    if not start:
        await update.message.reply_text("Usage: /backfill YYYY-MM-DD [YYYY-MM-DD]")
        return

    msg = await update.message.reply_text(f"⏳ Backfilling daily_metrics {start} → {end}…")
    try:
        from src.report import backfill_daily_metrics

        result = await asyncio.to_thread(backfill_daily_metrics, start, end)
        lines = ["✅ Backfill done (Shopify-only):"]
        for day, orders, rev in result:
            if orders == "ERR":
                lines.append(f"  • {day}: ❌ {rev}")
            else:
                lines.append(f"  • {day}: {orders} orders · ${rev:,.2f}")
        await msg.edit_text("\n".join(lines)[:3800])
    except Exception as exc:  # noqa: BLE001
        logger.exception("Errore nel backfill")
        await msg.edit_text(f"❌ Backfill error: {exc}")


async def cmd_audit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/audit YYYY-MM-DD (admin) -> scompone la revenue del giorno (tax/shipping/refund/boundary)."""
    if not _authorized(update):
        await update.message.reply_text("⛔️ Unauthorized chat.")
        return
    args = context.args or []
    if not args:
        await update.message.reply_text("Usage: /audit YYYY-MM-DD")
        return
    day = args[0]

    msg = await update.message.reply_text(f"⏳ Auditing {day}…")
    try:
        from src.diagnostics import day_audit

        text = await asyncio.to_thread(day_audit, day)
        await msg.edit_text(text[:3800])
        if len(text) > 3800:
            await _send_chunks(update.message, text[3800:])
    except Exception as exc:  # noqa: BLE001
        logger.exception("Errore nell'audit")
        await msg.edit_text(f"❌ Audit error: {exc}")


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
        await update.message.reply_text(_friendly_ai_error(exc))


def _friendly_ai_error(exc: Exception) -> str:
    """Messaggio chiaro per gli errori dell'AI (i comandi deterministici non la usano)."""
    msg = str(exc).lower()
    det = ("ℹ️ The deterministic commands don't use the AI and still work: "
           "/report · /audit · /backfill · /refresh_today · /pl · "
           "/meta_check · /google_check · /tw_check · /klaviyo_check.")
    if "credit balance" in msg or "billing" in msg or "too low" in msg:
        return ("🤖 AI is unavailable: the Anthropic account is out of credits.\n"
                "Add credits at console.anthropic.com → Plans & Billing.\n\n" + det)
    if "rate limit" in msg or "429" in msg or "overloaded" in msg:
        return "🤖 AI is busy (rate-limited/overloaded). Try again in a moment.\n\n" + det
    if "authentication" in msg or "invalid x-api-key" in msg or "401" in msg:
        return ("🤖 AI unavailable: invalid/missing ANTHROPIC_API_KEY on the bot service.\n\n"
                + det)
    return f"❌ AI error: {exc}\n\n{det}"


def build_application() -> Application:
    if not settings.TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN non configurato (.env).")
    app = Application.builder().token(settings.TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("report", cmd_report))
    app.add_handler(CommandHandler("report7", cmd_report7))
    app.add_handler(CommandHandler("report5", cmd_report5))
    app.add_handler(CommandHandler("reportmonth", cmd_reportmonth))
    app.add_handler(CommandHandler("pl", cmd_pl))
    app.add_handler(CommandHandler("klaviyo_check", cmd_klaviyo_check))
    app.add_handler(CommandHandler("meta_check", cmd_meta_check))
    app.add_handler(CommandHandler("tw_check", cmd_tw_check))
    app.add_handler(CommandHandler("google_check", cmd_google_check))
    app.add_handler(CommandHandler("refresh_today", cmd_refresh_today))
    app.add_handler(CommandHandler("backfill", cmd_backfill))
    app.add_handler(CommandHandler("audit", cmd_audit))
    # qualsiasi testo non-comando -> assistente AI
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    return app


def main() -> None:
    app = build_application()
    logger.info("Bot avviato. In ascolto dei comandi (/report).")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
