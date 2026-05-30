"""
Assistente conversazionale (linguaggio naturale) — Anthropic API.

REGOLA VINCOLANTE #1 del progetto: l'AI NON calcola MAI numeri.
Qui Claude riceve metriche **già calcolate** (lette da Supabase con codice
deterministico) e si limita a INTERPRETARLE, spiegarle e dare consigli in italiano.
Tutti i numeri nel contesto provengono dal database, non dal modello.

Pattern Anthropic:
- system prompt stabile con prompt caching (`cache_control: ephemeral`);
- i dati (volatili, cambiano a ogni richiesta) vanno nel messaggio utente,
  DOPO il prefisso cache-abile, così la cache del system prompt resta valida;
- adaptive thinking attivo (il modello decide quanto ragionare).
"""
from __future__ import annotations

import json
from typing import Optional

import anthropic

from config import settings
from src.db.supabase_client import SupabaseStore

# System prompt STABILE (cache-abile). Niente date/ID dinamici qui dentro,
# altrimenti il prefisso cambierebbe a ogni richiesta e la cache salterebbe.
SYSTEM_PROMPT = """\
You are the AI assistant for "Heritage Ring", an e-commerce store selling rings and bracelets.
Reply in ENGLISH by default. If the user clearly writes in another language, you may reply
in that language, but English is the default.

CORE RULES (binding):
1. NEVER calculate, estimate, or invent numbers. Use ONLY the values provided in the
   "DATA" block given with each message. Those numbers were already computed by
   deterministic code and are the single source of truth.
2. If a figure is not present in the DATA, say so explicitly ("I don't have that data")
   instead of estimating it. Do not derive numbers by difference or proportion.
3. The currency is ALWAYS USD ($). Do not convert currencies.
4. System status: connected sources are Shopify (orders, revenue, net profit),
   Meta Ads (spend, ROAS, CPA, per-CAMPAIGN breakdown), and Klaviyo EMAIL CAMPAIGNS
   (attributed revenue, opens, clicks, conversions, per-campaign breakdown).
   Google and TikTok are NOT connected yet: if asked about those, say the data isn't
   available. Meta data is at CAMPAIGN level only (no per-creative/per-ad breakdown).
   Klaviyo data is CAMPAIGNS ONLY — flows are NOT tracked: if asked about flows, say so.

WHAT YOU CAN DO:
- Explain and comment on the provided metrics (revenue, orders, AOV, COGS, operating and
  net profit, Meta spend/ROAS/CPA, per-campaign spend/revenue/orders/CVR, Klaviyo campaign
  revenue/opens/clicks/conversions/open_rate/click_rate).
- Point out trends by comparing the days present in the DATA.
- Flag ad campaigns that look like they're wasting money (e.g. spend with 0 purchases, or
  ROAS below break-even ~1.58x) and comment on email-campaign performance, using ONLY the
  figures in DATA. These are textual suggestions only — the system never changes the ad
  account or Klaviyo; the user decides and acts.
- Give qualitative, actionable advice, always stating which numbers you base it on.

Useful definitions:
- "operating" net profit = excluding the fixed-costs allocation.
- "net" net profit = including the daily fixed-costs allocation.
- AOV = average order value. ROAS = revenue / spend (Meta-reported). CPA = spend / orders.
  CVR = orders / clicks. Break-even ROAS ~ 1.58x.
- Klaviyo: revenue = conversion value attributed to the campaign; open_rate = opens /
  recipients; click_rate = clicks / recipients. Klaviyo metrics cover CAMPAIGNS only.

Format replies for Telegram (plain text, optional bullet lists).
"""


def _build_data_context(store: SupabaseStore, days: int = 14) -> str:
    """
    Costruisce il blocco DATI (testo) con le metriche già calcolate dal DB.
    Tutti i numeri qui dentro vengono dal database, non dal modello.
    """
    recent = store.get_recent_daily_metrics(days=days)
    meta_daily = store.get_recent_meta_daily(days=days)
    meta_campaigns = store.get_recent_meta_campaigns(days=7, limit=60)
    klaviyo_daily = store.get_recent_klaviyo_daily(days=days)
    klaviyo_campaigns = store.get_recent_klaviyo_campaigns(days=7, limit=60)

    if not recent and not meta_daily and not klaviyo_daily:
        return "DATA: (no metrics present in the database yet)"

    payload = {
        "currency": "USD",
        "connected_sources": "Shopify + Meta + Klaviyo email CAMPAIGNS (no flows); Google/TikTok not connected",
        "shopify_daily_recent": recent,                # ordinate dal più recente
        "meta_daily_recent": meta_daily,               # spend/ROAS/CPA per giorno (USD)
        "meta_campaigns_recent": meta_campaigns,       # breakdown per campagna (USD)
        "klaviyo_daily_recent": klaviyo_daily,         # revenue/opens/clicks per giorno (campagne)
        "klaviyo_campaigns_recent": klaviyo_campaigns, # breakdown per campagna email
        "field_notes": {
            "net_profit_operativo": "net profit excluding fixed costs",
            "net_profit_netto": "net profit including fixed-costs allocation",
            "ads_spend": "total ad spend subtracted from net profit (USD)",
            "meta.roas": "Meta-reported revenue / spend",
            "meta.cpa": "spend / purchases",
            "meta.cvr": "orders / clicks (campaign level)",
            "klaviyo.revenue": "conversion value attributed to email campaigns (USD)",
            "klaviyo.open_rate": "opens / recipients (campaign level)",
            "klaviyo.click_rate": "clicks / recipients (campaign level)",
            "klaviyo.note": "Klaviyo data is CAMPAIGNS ONLY — flows are not tracked",
            "break_even_roas": settings.BREAK_EVEN_ROAS,
        },
    }
    return "DATA (source: database, already computed — use ONLY these):\n" + json.dumps(
        payload, ensure_ascii=False, indent=2, default=str
    )


def answer_question(
    question: str,
    store: Optional[SupabaseStore] = None,
    days: int = 14,
) -> str:
    """
    Risponde a una domanda libera dell'utente.

    Legge le metriche dal DB (deterministico), le passa a Claude come contesto e
    chiede una risposta in linguaggio naturale. Claude non calcola numeri.
    """
    store = store or SupabaseStore()
    data_context = _build_data_context(store, days=days)

    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    response = client.messages.create(
        model=settings.ANTHROPIC_MODEL,
        max_tokens=2000,
        thinking={"type": "adaptive"},
        output_config={"effort": "medium"},
        system=[
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},  # cache del prefisso stabile
            }
        ],
        messages=[
            {
                "role": "user",
                # i dati (volatili) stanno DOPO il system cache-abile
                "content": f"{data_context}\n\n---\nDOMANDA DELL'UTENTE:\n{question}",
            }
        ],
    )

    text = next((b.text for b in response.content if b.type == "text"), "")
    return text.strip() or "Sorry, I couldn't produce an answer."
