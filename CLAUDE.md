# CLAUDE.md — Heritage Ring · Assistente AI

Regole operative del progetto per qualsiasi agente/sviluppatore che lavora su questo repo.

## Cos'è
Sistema sempre attivo che ogni notte tira i dati dalle piattaforme pubblicitarie e da
Shopify, calcola le metriche con **codice deterministico**, salva lo storico su database
e invia un report giornaliero + alert su Telegram. Risponde anche a domande libere nel bot.

## Stato attuale
- **Fase 1 in corso — SOLO Shopify.** Meta, Google, TikTok e Klaviyo sono per ora ignorati.
- Il primo obiettivo è far funzionare `/report` end-to-end con i dati Shopify di ieri.

## Stack
- **Linguaggio:** Python
- **Database:** Supabase (Postgres) — credenziali via env
- **Bot:** Telegram — token via env
- **Linguaggio naturale / risposte libere:** Anthropic API (Claude) — key via env,
  usata **SOLO per il linguaggio, MAI per calcolare metriche**
- **Scheduler:** cron giornaliero a **00:00 Europe/Rome**

## REGOLE VINCOLANTI

### 1. Mai AI sui numeri
Tutte le metriche (revenue, COGS, net profit, ROAS, CPA, AOV...) si calcolano con
**codice deterministico**. Claude serve solo a interpretare il linguaggio e formattare
le risposte libere. Nessun numero del report deve mai passare da un LLM.

### 2. Credenziali
- Tutte via variabili d'ambiente. **MAI nel codice, MAI su git.**
- Esiste `.env.example` come riferimento; `.env` è in `.gitignore`.
- Chiavi: `SHOPIFY_STORE`, `SHOPIFY_CLIENT_ID`, `SHOPIFY_CLIENT_SECRET`,
  `SHOPIFY_API_VERSION`, `SUPABASE_URL`, `SUPABASE_KEY`, `TELEGRAM_BOT_TOKEN`,
  `TELEGRAM_CHAT_ID`, `ANTHROPIC_API_KEY`.

### 3. Autenticazione Shopify (gennaio 2026+)
- Le custom app admin e il token statico `shpat_` sono **deprecati**.
- L'app è creata nel **Dev Dashboard** → fornisce **Client ID + Client Secret**.
- Si usa il **client credentials grant**: il backend scambia Client ID + Secret per un
  access token via codice ad ogni necessità (il token NON è copiabile dalla UI).
- Prerequisiti: (a) Admin API scopes abilitati (`read_orders`, `read_products`,
  `read_fulfillments`, `read_inventory`); (b) app installata sullo store.
- `SHOPIFY_API_VERSION = 2026-04`.

### 4. Sicurezza Meta (quando arriverà la Fase 1 Meta / Fase 2+)
- **Read-only**: solo `ads_read` e `read_insights`. MAI `ads_management`.
- **System User token** via Business Manager, non token personali.
- **Una sola chiamata insights al giorno** (report notturno). Niente loop/polling.
- Rispetta i rate limit (header `X-Ad-Account-Usage`, `X-Business-Use-Case-Usage`),
  gestisci errore 613, usa backoff. Il sistema **non agisce mai** sull'account: gli
  alert sulle creative sono solo suggerimenti testuali.

### 5. Valuta
- **Valuta base = USD.** Tutto si calcola, salva e mostra in USD.
- Spese di Meta/Google/TikTok riportate in EUR vanno convertite in USD prima di
  qualsiasi calcolo/salvataggio. Nessun valore nel report finale è in EUR.

## Formula Net Profit (codice puro)
```
net_profit_giorno =
    revenue_reale_shopify
  − COGS_totale            (somma per line item via mappa SKU/handle; $0 o sconosciuto = $3)
  − costi_spedizione       ($7 × numero_ordini)
  − fee_pagamenti          (7.5% × revenue)
  − spesa_ads_totale       (Meta + Google + TikTok — in Fase 1 Shopify = 0)
  − quota_costi_fissi      ($5.668 / 30 ≈ $188.93 al giorno)  [attivabile/disattivabile]
```
Il report mostra **sia** il net profit "operativo" (senza costi fissi) **sia** quello
"netto" (con la quota costi fissi).

## COGS — `config/cogs.yaml`
- È il cuore del calcolo costi. Match prodotto→costo per **handle Shopify** (preferito)
  o per titolo.
- **Regola di sicurezza:** qualsiasi prodotto non elencato o con costo $0 = **$3**.
- Prodotti custom hanno costo specifico; `classic-rings` e `bracelets` = $3 ciascuno.

## Parametri configurabili (`config/settings.py`, non hardcoded)
break_even_roas=1.58 · soglia_creative_spend=150 USD · soglia_creative_giorni=1 ·
meta_cpa_max=90 · meta_freq_max=1.5 · meta_finestra_giorni=5 ·
ordini_non_spediti_giorni=21 · ordini_non_spediti_soglia=100 ·
piattaforma_perdita_giorni_consecutivi=3 · cap_produzione_giornaliero=40 (solo personalized) ·
fee_pagamenti=0.075 · spedizione_per_ordine=7 · includi_costi_fissi_in_net_profit ·
fonte_roas="piattaforma".

## Comandi bot
- `/report` → report immediato (Fase 1: ordini + revenue + net profit di ieri da Shopify)
- `/pl ANNO MESE` → P&L mensile (fasi successive)
- messaggi liberi → risposta via Claude che legge il database (fasi successive)

## Fasi
1. **Fase 1 (MVP):** Shopify (+ Meta) → report giornaliero + alert 1,2,3. *Ora siamo qui,
   ma solo Shopify: verifica che i numeri tornino prima di proseguire.*
2. **Fase 2:** Google Ads + alert 4.
3. **Fase 3:** TikTok.
4. **Fase 4:** Klaviyo + alert 5 (scaling).

## Sanity-check economici (riferimento, NON hardcodare)
AOV storico ~$131.52 · contribution margin ~63.3% · break-even ROAS ~1.58x.
Servono solo a notare se un numero calcolato è palesemente sbagliato.

## Struttura repo
```
config/        cogs.yaml, settings.py
src/connectors shopify.py (client credentials grant)
src/db         supabase_client.py
src/metrics    profit.py (calcoli deterministici net profit)
src/bot        telegram_bot.py (/report)
supabase/migrations  SQL tabelle (orders, line items, daily metrics)
```
