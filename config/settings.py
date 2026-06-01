"""
Parametri configurabili del sistema (NON hardcoded nei moduli).
Tutti i valori monetari sono in USD. Valuta base = USD.

In Fase 1 (solo Shopify) molti parametri riguardano piattaforme ads non ancora
collegate: restano qui pronti, ma non influenzano il report Shopify.
"""
from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Credenziali via env (mai hardcoded, mai su git)
# ---------------------------------------------------------------------------
SHOPIFY_STORE = os.getenv("SHOPIFY_STORE", "")
SHOPIFY_CLIENT_ID = os.getenv("SHOPIFY_CLIENT_ID", "")
SHOPIFY_CLIENT_SECRET = os.getenv("SHOPIFY_CLIENT_SECRET", "")
SHOPIFY_API_VERSION = os.getenv("SHOPIFY_API_VERSION", "2026-04")

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
# Modello Claude per le risposte libere (solo linguaggio, mai i numeri).
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-opus-4-8")

# ---- Meta (Fase 2, SOLA LETTURA: ads_read + read_insights) ----
# System User token via Business Manager. MAI ads_management.
META_ACCESS_TOKEN = os.getenv("META_ACCESS_TOKEN", "")
META_AD_ACCOUNT_ID = os.getenv("META_AD_ACCOUNT_ID", "")
META_API_VERSION = os.getenv("META_API_VERSION", "v21.0")

# ---- Klaviyo (Fase 4, SOLA LETTURA: solo CAMPAGNE, NO flows) ----
# Private API key (pk_...). Una sola chiamata Reporting API al giorno, cache su DB.
KLAVIYO_API_KEY = os.getenv("KLAVIYO_API_KEY", "")
KLAVIYO_API_REVISION = os.getenv("KLAVIYO_API_REVISION", "2024-10-15")
# Metrica di conversione (di solito "Placed Order"). Se vuoto, viene risolta a runtime.
KLAVIYO_CONVERSION_METRIC_ID = os.getenv("KLAVIYO_CONVERSION_METRIC_ID", "")

# ---- Triple Whale (Fase 3, SOLA LETTURA: SOLO TikTok) ----
# API key con scope "Summary Page: Read" + "Pixel Attribution: Read".
# Una sola chiamata Summary al giorno, cache su DB. Estrae SOLO il canale TikTok.
TRIPLEWHALE_API_KEY = os.getenv("TRIPLEWHALE_API_KEY", "")
TRIPLEWHALE_API_BASE = os.getenv("TRIPLEWHALE_API_BASE", "https://api.triplewhale.com/api/v2")
TRIPLEWHALE_SUMMARY_PATH = os.getenv("TRIPLEWHALE_SUMMARY_PATH", "/summary-page/get-data")
# Opzionale: dominio/ID dello shop, se l'endpoint Summary lo richiede.
TRIPLEWHALE_SHOP_ID = os.getenv("TRIPLEWHALE_SHOP_ID", "")

TIMEZONE = os.getenv("TIMEZONE", "Europe/Rome")

# ---------------------------------------------------------------------------
# Parametri di business (configurabili)
# ---------------------------------------------------------------------------
# Net profit / costi
FEE_PAGAMENTI = 0.075          # 7.5% sul revenue
SPEDIZIONE_PER_ORDINE = 7      # USD flat per ordine
INCLUDI_COSTI_FISSI_IN_NET_PROFIT = True
COSTI_FISSI_MENSILI = 5668     # USD (3257 personale + 2411 software)
GIORNI_MESE_ALLOCAZIONE = 30   # quota giornaliera = COSTI_FISSI_MENSILI / 30

# Soglie alert (Fase 2+ per la parte ads, qui solo predisposte)
BREAK_EVEN_ROAS = 1.58
SOGLIA_CREATIVE_SPEND = 150           # USD
SOGLIA_CREATIVE_GIORNI = 1
META_CPA_MAX = 90                     # USD
META_FREQ_MAX = 1.5
META_FINESTRA_GIORNI = 5
ORDINI_NON_SPEDITI_GIORNI = 21
ORDINI_NON_SPEDITI_SOGLIA = 100
PIATTAFORMA_PERDITA_GIORNI_CONSECUTIVI = 3
CAP_PRODUZIONE_GIORNALIERO = 40       # solo prodotti personalized

# ROAS: in futuro si potrà passare a "shopify_reale"
FONTE_ROAS = "piattaforma"

# Cambio valuta (Fase 1 Shopify già in USD; pronto per gli ads in EUR)
EUR_TO_USD = float(os.getenv("EUR_TO_USD", "1.08"))

# ---------------------------------------------------------------------------
# Percorsi
# ---------------------------------------------------------------------------
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COGS_YAML_PATH = os.path.join(_BASE_DIR, "config", "cogs.yaml")
