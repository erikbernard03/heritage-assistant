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
4. System status: PHASE 1 — only Shopify data is connected (orders, revenue, net profit).
   The advertising platforms (Meta, Google, TikTok) and Klaviyo are NOT connected yet:
   if the user asks about campaigns, ROAS, CPA, or ad spend, explain that this data is
   not available yet in this phase.

WHAT YOU CAN DO:
- Explain and comment on the provided metrics (revenue, orders, AOV, COGS, operating and
  net profit, etc.).
- Point out trends by comparing the days present in the DATA.
- Give qualitative, actionable advice, always stating which numbers you base it on.

Useful definitions:
- "operating" net profit = excluding the fixed-costs allocation.
- "net" net profit = including the daily fixed-costs allocation.
- AOV = average order value.

Format replies for Telegram (plain text, optional bullet lists).
"""


def _build_data_context(store: SupabaseStore, days: int = 14) -> str:
    """
    Costruisce il blocco DATI (testo) con le metriche già calcolate dal DB.
    Tutti i numeri qui dentro vengono dal database, non dal modello.
    """
    recent = store.get_recent_daily_metrics(days=days)
    if not recent:
        return "DATI: (nessuna metrica giornaliera presente nel database)"

    payload = {
        "valuta": "USD",
        "fase": "1 (solo Shopify; Meta/Google/TikTok/Klaviyo non collegati)",
        "metriche_giornaliere_recenti": recent,  # già ordinate dal più recente
        "note_campi": {
            "net_profit_operativo": "net profit senza costi fissi",
            "net_profit_netto": "net profit con quota costi fissi",
            "ads_spend": "spesa pubblicitaria (in Fase 1 sempre 0)",
        },
    }
    return "DATI (fonte: database, già calcolati — usa solo questi):\n" + json.dumps(
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
