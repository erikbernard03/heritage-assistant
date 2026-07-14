# Deploy su Railway — Heritage Ring

**Tre** servizi nello **stesso progetto Railway**, dallo stesso repo/branch:

1. **`bot`** — worker **sempre attivo** (Telegram in ascolto 24/7, risposte AI + `/report` + `/pl`).
2. **`cron`** — **Cron Job** che ogni notte a mezzanotte (Europe/Rome) invia il report giornaliero.
3. **`dashboard`** — **web app Streamlit** (SOLA LETTURA) con dominio pubblico: mostra le stesse
   metriche dei report, filtrate per periodo, ottimizzata per mobile. Protetta da password.

Tutti usano lo stesso codice; cambia solo lo **Start Command** (e, per il cron, lo schedule).

---

## 0. Prerequisiti
- Account Railway collegato a GitHub.
- Repo `erikbernard03/heritage-assistant`, branch `claude/compassionate-lamport-JC2dT`.
- Le tabelle Supabase già create e l'app Shopify installata (fatto in Fase 1).

---

## 1. Crea il progetto e il primo servizio (BOT)
1. Railway → **New Project** → **Deploy from GitHub repo** → scegli `heritage-assistant`.
2. Quando chiede il branch, seleziona **`claude/compassionate-lamport-JC2dT`**
   (Service → **Settings → Source → Branch**).
3. Rinomina il servizio in **`bot`** (Settings → Service Name).
4. **Settings → Config-as-code → Railway Config File**: `railway.bot.json`
   (definisce già lo Start Command `python -m src.bot.telegram_bot`, 1 replica, restart On-Failure).
5. **Settings → Build → Custom Build Command**: lascialo **VUOTO** (Nixpacks builda da
   `requirements.txt`). ⚠️ Il Build Command NON deve mai essere uguale allo Start Command,
   altrimenti il deploy fallisce con *"buildCommand and startCommand cannot be the same"*.
6. *(Lascia il servizio senza dominio pubblico: è un worker, non serve HTTP.)*

> Se non usi il config file, in alternativa: **Settings → Deploy → Start Command** =
> `python -m src.bot.telegram_bot`, **Replicas = 1**, e **Build Command vuoto**.

## 2. Aggiungi il secondo servizio (CRON)
1. Nello **stesso progetto** → **+ New** → **GitHub Repo** → di nuovo `heritage-assistant`
   (stesso repo, stesso branch). Così hai due servizi nello stesso progetto.
2. Rinomina il servizio in **`cron`**.
3. **Settings → Config-as-code → Railway Config File**: `railway.cron.json`
   (definisce Start Command `python -m src.run_daily`, Cron `0 22,23 * * *`, restart Never).
4. **Settings → Build → Custom Build Command**: **VUOTO**. ⚠️ NON impostare qui
   `python -m src.run_daily`: il Build Command uguale allo Start Command fa fallire il deploy
   (*"buildCommand and startCommand cannot be the same"*).
5. Verifica che lo **Start Command** del cron sia `python -m src.run_daily` e **non** quello
   del bot: il cron NON deve mai avviare il polling Telegram (altrimenti due istanze del bot
   in `getUpdates` → errore *Conflict: terminated by other getUpdates request*).

> ### Perché `0 22,23 * * *` e non `0 0 * * *`?
> Il cron di Railway è in **UTC** e **non** gestisce l'ora legale. Roma è UTC+1 (inverno)
> o UTC+2 (estate). Il job parte alle 22:00 **e** alle 23:00 UTC, ma grazie alla
> variabile `RAILWAY_CRON_GUARD=1` **procede solo quando a Roma sono le 00:xx** ed esce
> subito nell'altro caso. Risultato: **esattamente un report al giorno, sempre a
> mezzanotte di Roma**, tutto l'anno. (La singola esecuzione "a vuoto" dura un istante.)

---

## 2b. Aggiungi il terzo servizio (DASHBOARD web)

Servizio web Streamlit, **SOLA LETTURA** sulle tabelle Supabase (non scrive mai). I numeri
combaciano sempre con `/report7` perché riusa la stessa aggregazione (`aggregate_period`).

1. Nello **stesso progetto** → **+ New** → **GitHub Repo** → di nuovo `heritage-assistant`
   (stesso repo, stesso branch `claude/compassionate-lamport-JC2dT`).
2. Rinomina il servizio in **`dashboard`** (Settings → Service Name).
3. **Settings → Config-as-code → Railway Config File**: `railway.dashboard.json`
   (Start Command `streamlit run src/dashboard/app.py --server.port $PORT --server.address 0.0.0.0`,
   1 replica, restart On-Failure).
4. **Settings → Build → Custom Build Command**: **VUOTO** (Nixpacks builda da `requirements.txt`,
   che ora include `streamlit` e `pandas`). ⚠️ NON metterci lo Start Command.
5. **Abilita il dominio pubblico**: Settings → **Networking → Public Networking** →
   **Generate Domain**. Railway assegna un URL `https://<nome>.up.railway.app` ed espone `$PORT`
   automaticamente (lo Start Command lo usa già). *(Facoltativo: Custom Domain con il tuo dominio.)*
6. **Variables** (vedi tabella sotto): al `dashboard` bastano `SUPABASE_URL`, `SUPABASE_KEY`,
   `TIMEZONE` e soprattutto **`DASHBOARD_PASSWORD`** (senza password la dashboard resta bloccata).
7. Apri l'URL sul telefono → inserisci la password → scegli il periodo (Last 7 days / This week /
   This month / Last month / All time / Custom range).

> La dashboard legge i dati **già salvati** dal cron notturno (nessuna pull live in v1):
> è veloce e non tocca le API esterne. Il pulsante **🔄 Refresh data** svuota la cache (TTL 15 min).

---

## 3. Variabili d'ambiente (Railway → servizio → Variables)

Inseriscile sui servizi indicati. Suggerimento: usa le **"Shared Variables"** del progetto
per condividere `SUPABASE_URL`, `SUPABASE_KEY` e `TIMEZONE` fra i tre servizi senza riscriverle.

La colonna `dash` indica cosa serve al **dashboard** (SOLA LETTURA): gli bastano Supabase +
timezone + la password. **Niente** chiavi Shopify/Meta/ecc.: non fa pull, legge solo il DB.

| Variabile | `bot` | `cron` | `dash` | Valore |
|---|:---:|:---:|:---:|---|
| `DASHBOARD_PASSWORD` | ⬜️ | ⬜️ | ✅ | (scegli una password robusta) — **senza, la dashboard è bloccata** |
| `SHOPIFY_STORE` | ✅ | ✅ | ⬜️ | `fx8bnw-ix.myshopify.com` |
| `SHOPIFY_CLIENT_ID` | ✅ | ✅ | ⬜️ | (il tuo Client ID) |
| `SHOPIFY_CLIENT_SECRET` | ✅ | ✅ | ⬜️ | (il tuo Client Secret) |
| `SHOPIFY_API_VERSION` | ✅ | ✅ | ⬜️ | `2026-04` |
| `SUPABASE_URL` | ✅ | ✅ | ✅ | `https://bwwwhjroexmjbbydtetd.supabase.co` |
| `SUPABASE_KEY` | ✅ | ✅ | ✅ | (la secret key `sb_secret_...`) |
| `TELEGRAM_BOT_TOKEN` | ✅ | ✅ | ⬜️ | (token BotFather) |
| `TELEGRAM_CHAT_ID` | ✅ | ✅ | ⬜️ | `598516150` |
| `ANTHROPIC_API_KEY` | ✅ | ⬜️ | ⬜️ | (la tua key `sk-ant-...`) — serve solo al bot |
| `ANTHROPIC_MODEL` | ✅ | ⬜️ | ⬜️ | `claude-opus-4-8` (opzionale, è il default) |
| `META_ACCESS_TOKEN` | ✅ | ✅ | ⬜️ | System User token (Business Manager, read-only) |
| `META_AD_ACCOUNT_ID` | ✅ | ✅ | ⬜️ | ID account pubblicitario (es. `act_123…` o `123…`) |
| `META_API_VERSION` | ✅ | ✅ | ⬜️ | `v21.0` (opzionale, è il default) |
| `KLAVIYO_API_KEY` | ✅ | ✅ | ⬜️ | Private API key `pk_…` (read-only) |
| `KLAVIYO_API_REVISION` | ✅ | ✅ | ⬜️ | `2024-10-15` (opzionale, è il default) |
| `KLAVIYO_CONVERSION_METRIC_ID` | ⬜️ | ⬜️ | ⬜️ | (opzionale) ID metrica "Placed Order"; se vuoto è risolto a runtime |
| `TRIPLEWHALE_API_KEY` | ✅ | ✅ | ⬜️ | API key (scope Summary Page: Read + Pixel Attribution: Read) — solo TikTok |
| `TRIPLEWHALE_SHOP_ID` | ⬜️ | ⬜️ | ⬜️ | (opzionale) dominio shop se l'endpoint Summary lo richiede |
| `TIMEZONE` | ✅ | ✅ | ✅ | `Europe/Rome` |
| `RAILWAY_CRON_GUARD` | ⬜️ | ✅ | ⬜️ | `1` — **solo sul cron** |

> **Meta (Fase 2):** esegui anche la migration `supabase/migrations/002_meta.sql` nel
> SQL Editor di Supabase (crea le tabelle `meta_daily` e `meta_campaigns`). Le variabili
> `META_*` vanno su **entrambi** i servizi: il `cron` fa la pull insights notturna,
> il `bot` la riusa dalla cache DB (1 sola chiamata Meta al giorno).

> **Klaviyo (Fase 4):** esegui anche `supabase/migrations/003_klaviyo.sql` (tabelle
> `klaviyo_daily` e `klaviyo_campaigns`). SOLO dati a livello CAMPAGNA (no flows).
> `KLAVIYO_*` su **entrambi** i servizi: il `cron` fa la pull reporting notturna,
> il `bot` la riusa dalla cache DB (1 sola chiamata Klaviyo al giorno).

> **TikTok via Triple Whale (Fase 3):** esegui anche `supabase/migrations/004_tiktok.sql`
> (tabelle `tiktok_daily` e `tiktok_campaigns`). Estrae SOLO il canale TikTok dal Summary.
> `TRIPLEWHALE_*` su **entrambi** i servizi: il `cron` fa la pull notturna, il `bot` la
> riusa dalla cache DB (1 sola chiamata Triple Whale al giorno). La spesa TikTok viene
> sottratta dal net profit (come Meta). Diagnostica: comando `/tw_check`.

> **Google Ads via Triple Whale (Fase 2):** esegui anche `supabase/migrations/005_google.sql`
> (tabella `google_daily`, solo totali account — no per-campaign per ora). Usa la stessa
> `TRIPLEWHALE_API_KEY`. La spesa Google viene sottratta dal net profit. Diagnostica:
> comando `/google_check`.

> **Store CVR da Shopify:** esegui `supabase/migrations/006_store_cvr.sql` e
> `007_store_cvr_daily.sql`. La CVR di negozio arriva da Shopify (ShopifyQL `FROM sessions`)
> per combaciare col dashboard; serve lo scope **`read_reports`** sull'app Shopify
> (aggiunto in `shopify.app.toml`): rilascia con `shopify app deploy` e **reinstalla** l'app.
> Senza `read_reports` si usa automaticamente il fallback Triple Whale (`pixelConversionRate`).
> Verifica con `/google_check` (mostra se la CVR Shopify funziona o serve il permesso).

> 🔒 Non committare mai questi valori: vivono solo nelle Variables di Railway (e nel tuo `.env` locale).

---

## 4. Deploy e verifica
1. Salva: Railway fa il build (Nixpacks legge `requirements.txt`, Python da `.python-version`).
2. **Bot**: apri i **Logs** del servizio `bot` → devi vedere
   `Bot avviato. In ascolto dei comandi (/report).` Poi su Telegram scrivi `/report`,
   `/pl 2026 5`, o una domanda libera ("come è andato ieri?").
3. **Cron**: nei **Logs** del servizio `cron`, alle esecuzioni schedulate vedrai
   o `report inviato.` (alla mezzanotte di Roma) o `non è mezzanotte a Roma … salto.`
   Per un test immediato puoi lanciare un deploy manuale del `cron` **senza**
   `RAILWAY_CRON_GUARD` (o impostandolo a `0`): invierà subito il report.
4. **Dashboard**: nei **Logs** del servizio `dashboard` cerca `You can now view your Streamlit app`.
   Apri l'URL pubblico (Settings → Networking) sul telefono → inserisci `DASHBOARD_PASSWORD` →
   verifica che i numeri di "Last 7 days" combacino con `/report7` su Telegram.

---

## Riepilogo comandi/valori
- Config file **bot**: `railway.bot.json` · Start: `python -m src.bot.telegram_bot` · Replicas: 1
- Config file **cron**: `railway.cron.json` · Start: `python -m src.run_daily` · Cron: `0 22,23 * * *`
- Config file **dashboard**: `railway.dashboard.json` · Start: `streamlit run src/dashboard/app.py --server.port $PORT --server.address 0.0.0.0` · Replicas: 1 · **Dominio pubblico ON**
- Build Command: **VUOTO** su tutti e tre (Nixpacks builda da `requirements.txt`)
- Guardia mezzanotte Roma: `RAILWAY_CRON_GUARD=1` (solo sul cron)
- Password dashboard: `DASHBOARD_PASSWORD` (solo sul dashboard)

## Troubleshooting
- **"buildCommand and startCommand cannot be the same"** → sul servizio hai un **Build
  Command** uguale allo **Start Command**. Svuota il Build Command (Settings → Build) e
  tieni solo lo Start Command (o usa i config file `railway.*.json` che non hanno buildCommand).
- **"Conflict: terminated by other getUpdates request"** → ci sono **due istanze del bot**
  in polling. Verifica che: (a) il servizio `bot` abbia **Replicas = 1**; (b) il servizio
  `cron` usi `python -m src.run_daily` e **non** il comando del bot. `run_daily` invia il
  report via HTTP (`sendMessage`) e **non** avvia mai il polling, quindi il conflitto nasce
  solo da una mis-configurazione del comando di avvio o da repliche multiple.
- **Dashboard: "🔒 Dashboard non configurata: manca DASHBOARD_PASSWORD"** → imposta
  `DASHBOARD_PASSWORD` nelle Variables del servizio `dashboard` e ri-deploya.
- **Dashboard non raggiungibile / 502** → verifica di aver **generato il dominio pubblico**
  (Settings → Networking) e che lo Start Command usi `--server.port $PORT --server.address 0.0.0.0`
  (Railway inietta `$PORT`; senza, Streamlit ascolta sulla porta sbagliata).
- **Dashboard "No data for the selected period"** → il periodo scelto non ha righe in
  `daily_metrics`. Usa "All time" o allarga il range; se manca lo storico, lancia `/backfill`.
