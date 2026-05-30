# Heritage Ring — Assistente AI

Sistema che ogni notte tira i dati dalle piattaforme, calcola le metriche con
**codice deterministico** (mai AI sui numeri), salva lo storico su Supabase e invia
report + alert su Telegram.

> **Stato: Fase 1 (MVP) — SOLO Shopify.** Meta, Google, TikTok, Klaviyo arrivano dopo.

Regole complete del progetto: vedi [`CLAUDE.md`](./CLAUDE.md).

## Struttura
```
config/        cogs.yaml (tabella costi), settings.py (parametri configurabili)
src/connectors shopify.py — client credentials grant
src/db         supabase_client.py — orders, line_items, daily_metrics
src/metrics    profit.py — calcolo deterministico net profit
src/bot        telegram_bot.py — comando /report
src/report.py  orchestratore (ieri -> Shopify -> metriche -> Telegram)
src/run_daily.py  job notturno (cron 00:00 Europe/Rome)
supabase/migrations/001_init.sql  schema database
tests/         test deterministici del net profit
```

## Setup
1. **Dipendenze**
   ```bash
   python3 -m venv .venv && . .venv/bin/activate
   pip install -r requirements.txt
   ```
2. **Credenziali** — copia `.env.example` in `.env` e compila i valori
   (il `.env` è in `.gitignore`, non va mai committato).
3. **Shopify (client credentials grant)** — l'app è nel Dev Dashboard e fornisce
   `Client ID` + `Client Secret`. Prima che funzioni:
   - abilita gli Admin API scopes: `read_orders, read_products, read_fulfillments, read_inventory`
     (vedi `shopify.app.toml`, poi `shopify app deploy`);
   - **installa** l'app sullo store dal Dev Dashboard.
4. **Supabase** — esegui `supabase/migrations/001_init.sql` nel SQL Editor.

## Test
```bash
pytest -q                      # math del net profit (no rete, no credenziali)
python -m src.report           # genera il report di ieri da Shopify reale (stampa a video)
python -m src.bot.telegram_bot # avvia il bot, poi scrivi /report in chat
```

## Net profit (formula)
```
net_profit = revenue − COGS − spedizione($7/ordine) − fee(7.5%) − ads − [costi_fissi/30]
```
Il report mostra net profit **operativo** (senza costi fissi) e **netto** (con costi fissi).
Tutti i valori in **USD**.
