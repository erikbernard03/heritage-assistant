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
Sei l'assistente AI di "Heritage Ring", un e-commerce di anelli e bracciali.
Parli SEMPRE in italiano, in modo chiaro, sintetico e concreto.

REGOLE FONDAMENTALI (vincolanti):
1. NON calcolare, stimare o inventare MAI numeri. Usa ESCLUSIVAMENTE i valori
   presenti nel blocco "DATI" che ti viene fornito a ogni messaggio. Quei numeri
   sono già stati calcolati da codice deterministico e sono la sola verità.
2. Se un dato non è presente nei DATI, dillo esplicitamente ("non ho questo dato")
   invece di stimarlo. Non dedurre numeri per differenza o proporzione.
3. La valuta è SEMPRE USD ($). Non convertire valute.
4. Stato del sistema: FASE 1 — sono collegati solo i dati Shopify (ordini, revenue,
   net profit). Le piattaforme pubblicitarie (Meta, Google, TikTok) e Klaviyo NON
   sono ancora collegate: se l'utente chiede di campagne, ROAS, CPA o spesa ads,
   spiega che quei dati non sono ancora disponibili in questa fase.

COSA PUOI FARE:
- Spiegare e commentare le metriche fornite (revenue, ordini, AOV, COGS, net profit
  operativo e netto, ecc.).
- Notare trend confrontando i giorni presenti nei DATI.
- Dare consigli qualitativi e operativi, dichiarando sempre su quali numeri ti basi.

Definizioni utili:
- net profit "operativo" = senza la quota dei costi fissi.
- net profit "netto" = con la quota giornaliera dei costi fissi inclusa.
- AOV = valore medio dell'ordine.

Formatta le risposte per Telegram (testo semplice, eventuali elenchi puntati).
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
    return text.strip() or "Non sono riuscito a formulare una risposta."
