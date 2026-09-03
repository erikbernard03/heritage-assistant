"""
Test del menu comandi Telegram (set_my_commands). Nessuna rete.

Richiede python-telegram-bot (installato in produzione): in ambienti senza il pacchetto
il test viene saltato automaticamente.
"""
import pytest

pytest.importorskip("telegram")


EXPECTED = [
    ("today", "Today so far (live intraday)"),
    ("yesterday", "Yesterday (live single-day)"),
    ("report", "Daily report (live pull)"),
    ("report5", "Last 5 days aggregated"),
    ("report7", "Last 7 days aggregated"),
    ("reportmonth", "Month to date"),
    ("reportlastmonth", "Full previous month"),
    ("refresh_today", "Force re-pull today + yesterday"),
    ("refresh_meta", "Re-bucket Meta for a date range"),
    ("backfill", "Re-pull Shopify for a date range"),
    ("pl", "Monthly P&L (year month)"),
    ("shopify_check", "Shopify scopes + orders/sessions probe"),
    ("stripe", "Stripe — fees, payouts, disputes, reconciliation"),
    ("backfill_stripe", "Backfill Stripe for a date range"),
    ("meta_check", "Meta diagnostic"),
    ("google_check", "Google diagnostic"),
    ("tw_check", "Triple Whale diagnostic"),
    ("klaviyo_check", "Klaviyo diagnostic"),
]


def test_bot_commands_match_expected_menu():
    from src.bot.telegram_bot import BOT_COMMANDS

    assert BOT_COMMANDS == EXPECTED


def test_bot_commands_build_valid_botcommand_objects():
    from telegram import BotCommand

    from src.bot.telegram_bot import BOT_COMMANDS

    objs = [BotCommand(c, d) for c, d in BOT_COMMANDS]
    # Telegram: nomi comando validi (minuscolo, [a-z0-9_], ≤32) e descrizioni non vuote
    for o in objs:
        assert 1 <= len(o.command) <= 32
        assert o.command == o.command.lower()
        assert all(ch.islower() or ch.isdigit() or ch == "_" for ch in o.command)
        assert 1 <= len(o.description) <= 256


def test_every_menu_command_has_a_registered_handler(monkeypatch):
    """Ogni comando del menu deve avere un CommandHandler registrato (niente voci morte)."""
    monkeypatch.setattr("config.settings.TELEGRAM_BOT_TOKEN", "123:abc")
    from telegram.ext import CommandHandler

    from src.bot.telegram_bot import BOT_COMMANDS, build_application

    app = build_application()
    registered = set()
    for handlers in app.handlers.values():
        for h in handlers:
            if isinstance(h, CommandHandler):
                registered |= set(h.commands)
    missing = [c for c, _ in BOT_COMMANDS if c not in registered]
    assert not missing, f"comandi nel menu senza handler: {missing}"
