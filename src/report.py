"""
Orchestratore del report giornaliero (Fase 1 Shopify + Fase 2 Meta).

Flusso:
  1. calcola l'intervallo "ieri" in Europe/Rome
  2. tira gli ordini da Shopify (client credentials grant)
  3. costruisce la mappa product_id -> handle (per il COGS)
  4. Meta (sola lettura): una pull insights/giorno (cache su DB), spesa in USD
  5. calcola le metriche deterministiche (net profit con spesa ads sottratta, AOV, ...)
  6. (opzionale) salva ordini/line items/metriche/Meta su Supabase
  7. formatta il messaggio Telegram (Shopify + Meta: spend/ROAS/CPA + campagne)

Nessun LLM tocca i numeri.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Optional

import pytz

from config import settings
from src.connectors.shopify import ShopifyConnector
from src.metrics.profit import DailyMetrics, compute_daily_metrics


@dataclass
class DayWindow:
    day_str: str            # YYYY-MM-DD (Europe/Rome)
    start: datetime         # inizio giorno, tz-aware
    end: datetime           # fine giorno (esclusiva), tz-aware


def day_window(target_date) -> DayWindow:
    """Intervallo [00:00, 24:00) di un giorno specifico in Europe/Rome, tz-aware."""
    tz = pytz.timezone(settings.TIMEZONE)
    start = tz.localize(datetime.combine(target_date, time.min))
    end = start + timedelta(days=1)
    return DayWindow(day_str=target_date.isoformat(), start=start, end=end)


def yesterday_window(now: Optional[datetime] = None) -> DayWindow:
    """Intervallo [00:00, 24:00) di IERI in Europe/Rome, tz-aware."""
    tz = pytz.timezone(settings.TIMEZONE)
    now = now.astimezone(tz) if now else datetime.now(tz)
    return day_window((now - timedelta(days=1)).date())


def refresh_today_and_yesterday() -> str:
    """
    Force re-pull di TUTTE le piattaforme per OGGI e IERI, sovrascrive le righe DB,
    e ritorna il report (di IERI, quello canonico) già rigenerato e aggiornato.
    """
    tz = pytz.timezone(settings.TIMEZONE)
    today = datetime.now(tz).date()
    yesterday = today - timedelta(days=1)
    # IERI: report canonico (ritornato per l'invio)
    _, y_text = build_daily_report(window=day_window(yesterday), persist=True)
    # OGGI: aggiorna le righe del giorno corrente (parziale) nel DB
    try:
        build_daily_report(window=day_window(today), persist=True)
    except Exception as exc:  # noqa: BLE001 — non bloccare l'invio del report di ieri
        print(f"[refresh] refresh di oggi saltato: {exc}")
    return y_text


def build_daily_report(
    window: Optional[DayWindow] = None,
    persist: bool = True,
) -> tuple[DailyMetrics, str]:
    """Costruisce le metriche + il testo del report per la finestra indicata (default: ieri)."""
    window = window or yesterday_window()

    # Shopify: se fallisce/va in timeout, si degrada (0 ordini) e il report parte comunque.
    shop = None
    try:
        shop = ShopifyConnector()
        orders = shop.get_orders(window.start, window.end)
        handle_map = shop.get_products_handle_map()
    except Exception as exc:  # noqa: BLE001 — il report deve arrivare comunque
        print(f"[report] Shopify pull failed: {exc}")
        orders, handle_map = [], {}

    # annota il giorno Europe/Rome su ogni ordine (per la persistenza)
    for o in orders:
        o["_day_rome"] = window.day_str

    # UNA sola pull del Summary Triple Whale per run: lo stesso oggetto dict alimenta
    # TikTok, Google e la CVR di negozio. Niente cache-by-key (che poteva non combaciare):
    # passiamo direttamente il dict. Tra run diversi si ri-tira sempre (no stale).
    tw_summary = _fetch_tw_summary(window.start.date().isoformat(), window.day_str)

    # Meta (Fase 2): pull insights del giorno; spesa in USD. Sempre UPSERT (overwrite).
    meta_daily, meta_campaigns, meta_spend = _load_meta(window.day_str, persist=persist)

    # TikTok via Triple Whale (Fase 3): SOLO TikTok. Spesa sottratta dal net profit.
    tiktok_daily, tiktok_campaigns, tiktok_spend = _load_tiktok(
        window.day_str, tw_summary, persist=persist
    )

    # Google Ads via Triple Whale (Fase 2): SOLO totali account + CVR di negozio.
    google_daily, google_spend = _load_google(
        window.day_str, tw_summary, persist=persist
    )

    # Klaviyo (Fase 4): SOLO campagne; revenue attribuita (NON entra nel net profit).
    klaviyo_daily, klaviyo_campaigns = _load_klaviyo(
        window.day_str, window.start.isoformat(), window.end.isoformat(), persist=persist
    )

    metrics = compute_daily_metrics(
        day=window.day_str,
        orders=orders,
        handle_map=handle_map,
        # spesa ads totale (Meta + TikTok + Google, USD) sottratta dal net profit
        ads_spend=meta_spend + tiktok_spend + google_spend,
    )

    # Store CVR: Shopify (sessioni) PRIMARIO per combaciare col dashboard; Triple Whale
    # (pixelConversionRate, in google_daily.store_cvr) come fallback.
    shopify_cvr = _load_shopify_cvr(shop, window.day_str)
    tw_cvr = float(google_daily.get("store_cvr") or 0) if google_daily else 0.0
    metrics.store_cvr = shopify_cvr if shopify_cvr is not None else tw_cvr

    if persist:
        _persist(orders, handle_map, metrics)

    # Break-even ROAS/CPA dalla media dei 4 giorni PRECEDENTI (da daily_metrics).
    breakeven = _load_breakeven(window.day_str)

    return metrics, format_report(
        metrics, meta_daily, meta_campaigns, klaviyo_daily, klaviyo_campaigns,
        tiktok_daily, tiktok_campaigns, google_daily, breakeven=breakeven,
    )


def _load_shopify_cvr(shop, day: str) -> Optional[float]:
    """
    CVR di negozio da Shopify (ShopifyQL FROM sessions). Frazione, o None se non
    disponibile (es. scope read_reports mancante / accesso negato) -> si userà il
    fallback Triple Whale.
    """
    if shop is None:
        return None
    try:
        return shop.get_session_conversion_rate(day)
    except Exception as exc:  # noqa: BLE001
        print(f"[report] Shopify CVR (sessions) non disponibile: {exc}")
        return None


def _load_breakeven(day: str):
    """Legge i 4 giorni precedenti da daily_metrics e calcola (break-even ROAS, CPA)."""
    try:
        from datetime import date as _date, timedelta as _td

        from src.db.supabase_client import SupabaseStore
        from src.metrics.profit import compute_breakeven

        d = _date.fromisoformat(day)
        start = (d - _td(days=4)).isoformat()   # 4 giorni precedenti
        end = (d - _td(days=1)).isoformat()
        rows = SupabaseStore().get_daily_metrics_range(start, end)
        return compute_breakeven(rows)
    except Exception as exc:  # noqa: BLE001 — il report deve arrivare comunque
        print(f"[report] break-even non calcolato: {exc}")
        return None, None


def _load_meta(day: str, persist: bool):
    """
    Restituisce (meta_daily_dict | None, meta_campaigns: list[dict], meta_spend_usd).

    Una pull insights per run (Meta) e SEMPRE upsert: la riga del giorno viene
    sovrascritta ad ogni esecuzione (così i dati restano aggiornati). Se Meta non è
    configurato o fallisce, il report prosegue in modalità solo-Shopify (spend=0).
    """
    if not (settings.META_ACCESS_TOKEN and settings.META_AD_ACCOUNT_ID):
        return None, [], 0.0

    store = None
    try:
        from src.db.supabase_client import SupabaseStore

        store = SupabaseStore()
    except Exception as exc:  # noqa: BLE001
        print(f"[report] Supabase non disponibile per Meta: {exc}")

    try:
        from src.connectors.meta import MetaConnector
        from src.metrics.meta import compute_meta_metrics

        meta = MetaConnector()
        currency = meta.get_account_currency()
        raw = meta.get_daily_campaign_insights(day)
        computed = compute_meta_metrics(day, raw, account_currency=currency)

        if persist and store is not None:
            store.upsert_meta_daily(computed)
            store.upsert_meta_campaigns(computed)

        daily_dict = computed.as_db_row()
        campaign_dicts = [c.as_db_row() for c in computed.campaigns]
        return daily_dict, campaign_dicts, computed.spend
    except Exception as exc:  # noqa: BLE001 — il report deve arrivare comunque
        print(f"[report] pull Meta saltata: {exc}")
        return None, [], 0.0


def _fetch_tw_summary(start: str, end: str) -> Optional[dict]:
    """
    UNA pull del Summary Triple Whale per run. Ritorna il dict (condiviso da TikTok,
    Google e CVR) o None se Triple Whale non è configurato / la chiamata fallisce.
    """
    if not settings.TRIPLEWHALE_API_KEY:
        return None
    try:
        from src.connectors.triplewhale import TripleWhaleConnector

        return TripleWhaleConnector().get_summary(start, end)
    except Exception as exc:  # noqa: BLE001 — il report deve arrivare comunque
        print(f"[report] pull Summary Triple Whale fallita: {exc}")
        return None


def _load_tiktok(day: str, summary: Optional[dict], persist: bool):
    """
    Restituisce (tiktok_daily_dict | None, tiktok_campaigns: list[dict], tiktok_spend_usd).

    Usa il `summary` già tirato (condiviso con Google). SOLO TikTok. SEMPRE upsert:
    la riga del giorno viene sovrascritta ad ogni run.
    """
    if summary is None:
        return None, [], 0.0

    store = None
    try:
        from src.db.supabase_client import SupabaseStore

        store = SupabaseStore()
    except Exception as exc:  # noqa: BLE001
        print(f"[report] Supabase non disponibile per TikTok: {exc}")

    try:
        from src.connectors.triplewhale import extract_tiktok
        from src.metrics.tiktok import compute_tiktok_metrics

        tiktok = extract_tiktok(summary)
        if not tiktok:
            print("[report] nessun canale TikTok trovato nel Summary di Triple Whale.")
            return None, [], 0.0

        computed = compute_tiktok_metrics(day, tiktok)
        if persist and store is not None:
            store.upsert_tiktok_daily(computed)
            store.upsert_tiktok_campaigns(computed)

        return (
            computed.as_db_row(),
            [c.as_db_row() for c in computed.campaigns],
            computed.spend,
        )
    except Exception as exc:  # noqa: BLE001 — il report deve arrivare comunque
        print(f"[report] elaborazione TikTok saltata: {exc}")
        return None, [], 0.0


def _load_google(day: str, summary: Optional[dict], persist: bool):
    """
    Restituisce (google_daily_dict | None, google_spend_usd).

    Usa lo STESSO `summary` di TikTok (passato dal chiamante). SOLO totali Google
    (no per-campaign) + CVR di negozio. SEMPRE upsert: la riga del giorno viene
    sovrascritta ad ogni run.
    """
    if summary is None:
        return None, 0.0

    store = None
    try:
        from src.db.supabase_client import SupabaseStore

        store = SupabaseStore()
    except Exception as exc:  # noqa: BLE001
        print(f"[report] Supabase non disponibile per Google: {exc}")

    try:
        from src.connectors.triplewhale import extract_google, extract_store_cvr
        from src.metrics.google import compute_google_metrics

        google = extract_google(summary)
        cvr = extract_store_cvr(summary)
        if not google and cvr is None:
            print("[report] nessuna metrica Google/CVR trovata nel Summary di Triple Whale.")
            return None, 0.0

        # anche se mancano le metriche Google, salviamo comunque la CVR di negozio
        computed = compute_google_metrics(day, google or {}, store_cvr=cvr or 0.0)
        if persist and store is not None:
            store.upsert_google_daily(computed)
        return computed.as_db_row(), computed.spend
    except Exception as exc:  # noqa: BLE001 — il report deve arrivare comunque
        print(f"[report] elaborazione Google saltata: {exc}")
        return None, 0.0


def _load_klaviyo(day: str, start_iso: str, end_iso: str, persist: bool):
    """
    Restituisce (klaviyo_daily_dict | None, klaviyo_campaigns: list[dict]).

    SOLO CAMPAGNE (no flows). SEMPRE upsert: la riga del giorno viene sovrascritta ad
    ogni run. Se Klaviyo non è configurato o fallisce, il report prosegue senza la sezione.
    """
    if not settings.KLAVIYO_API_KEY:
        return None, []

    store = None
    try:
        from src.db.supabase_client import SupabaseStore

        store = SupabaseStore()
    except Exception as exc:  # noqa: BLE001
        print(f"[report] Supabase non disponibile per Klaviyo: {exc}")

    try:
        from src.connectors.klaviyo import KlaviyoConnector
        from src.metrics.klaviyo import compute_klaviyo_metrics

        kc = KlaviyoConnector()
        metric_id = kc.resolve_conversion_metric_id()
        raw = kc.get_daily_campaign_report(start_iso, end_iso, metric_id)
        ids = [
            str((r.get("groupings") or {}).get("campaign_id") or "")
            for r in raw
            if (r.get("groupings") or {}).get("campaign_id")
        ]
        names = kc.get_campaign_names(ids)
        computed = compute_klaviyo_metrics(day, raw, names=names)

        if persist and store is not None:
            store.upsert_klaviyo_daily(computed)
            store.upsert_klaviyo_campaigns(computed)

        return computed.as_db_row(), [c.as_db_row() for c in computed.campaigns]
    except Exception as exc:  # noqa: BLE001 — il report deve arrivare comunque
        print(f"[report] pull Klaviyo saltata: {exc}")
        return None, []


def _persist(orders: list[dict], handle_map: dict[int, str], metrics: DailyMetrics) -> None:
    """Salva su Supabase se configurato; non blocca il report in caso di assenza DB."""
    try:
        from src.db.supabase_client import SupabaseStore

        store = SupabaseStore()
        store.upsert_orders(orders, handle_map)
        store.upsert_line_items(metrics)
        store.upsert_daily_metrics(metrics)
    except Exception as exc:  # il report deve arrivare comunque
        print(f"[report] persistenza Supabase saltata: {exc}")


def _fmt_cvr(cvr_fraction: Optional[float]) -> str:
    """CVR di negozio formattata in % (frazione -> percentuale). 'n/a' se assente/0."""
    cvr = float(cvr_fraction or 0)
    return f"{cvr * 100:.2f}%" if cvr > 0 else "n/a"


def _breakeven_line(breakeven: Optional[tuple]) -> str:
    """Riga break-even ROAS/CPA (media 4 giorni). 'n/a' se non calcolabile."""
    be_roas, be_cpa = (breakeven or (None, None))
    roas_s = f"{be_roas:,.2f}x" if be_roas else "n/a"
    cpa_s = f"${be_cpa:,.2f}" if be_cpa is not None else "n/a"
    return f"⚖️ Break-even ROAS: {roas_s} · Break-even CPA: {cpa_s} (4-day avg)"


def _roas_cpa_line(emoji: str, name: str, daily: Optional[dict]) -> Optional[str]:
    """Riga compatta ROAS/CPA per la Sezione 1 (None se la piattaforma non ha dati)."""
    if not daily:
        return None
    roas = float(daily.get("roas") or 0)
    orders = int(daily.get("orders") or 0)
    cpa = float(daily.get("cpa") or 0)
    cpa_str = f"${cpa:,.2f}" if orders > 0 else "n/a"
    return f"{emoji} {name} — ROAS {roas:,.2f}x · CPA {cpa_str}"


def format_report(
    m: DailyMetrics,
    meta_daily: Optional[dict] = None,
    meta_campaigns: Optional[list[dict]] = None,
    klaviyo_daily: Optional[dict] = None,
    klaviyo_campaigns: Optional[list[dict]] = None,
    tiktok_daily: Optional[dict] = None,
    tiktok_campaigns: Optional[list[dict]] = None,
    google_daily: Optional[dict] = None,
    breakeven: Optional[tuple] = None,
) -> str:
    """
    Report Telegram in 3 sezioni (Markdown, tutto in USD):
      1) KEY METRICS  2) INCOME & COSTS  3) PER-PLATFORM AD BREAKDOWN
    Tutti i numeri sono deterministici (nessun LLM).
    """
    out: list[str] = [f"📊 *Daily report — {m.day}* _(USD)_"]

    # ---- SEZIONE 1 — KEY METRICS --------------------------------------------
    out.append("\n*1) KEY METRICS*")
    out.append(
        f"💵 Net profit — operating *${m.net_profit_operativo:,.2f}* · "
        f"net *${m.net_profit_netto:,.2f}*"
    )
    out.append(f"💰 Revenue: *${m.revenue:,.2f}*")
    out.append(f"🛒 Orders: *{m.num_orders}*")
    out.append(f"🧾 AOV: ${m.aov:,.2f}")
    out.append(f"📈 Store CVR: {_fmt_cvr(m.store_cvr)}")
    out.append(_breakeven_line(breakeven))
    for line in (
        _roas_cpa_line("📣", "Meta", meta_daily),
        _roas_cpa_line("🎵", "TikTok", tiktok_daily),
        _roas_cpa_line("🔎", "Google", google_daily),
    ):
        if line:
            out.append(line)
    if klaviyo_daily:
        kla_rev = float(klaviyo_daily.get("revenue") or 0)
        out.append(f"✉️ Klaviyo campaign revenue: ${kla_rev:,.2f}")

    # ---- SEZIONE 2 — COST BREAKDOWN -----------------------------------------
    # NB: Revenue (= total_price) include GIÀ IVA + spedizione incassata: quel denaro
    # è dentro revenue e quindi nel net profit UNA SOLA VOLTA. Niente righe income
    # separate (evita il doppio conteggio); le mostriamo solo come parte dei costi.
    out.append("\n*2) COST BREAKDOWN*")
    out.append(f"   • Product COGS: −${m.cogs_total:,.2f}")
    out.append(f"   • Shipping cost ($7 × {m.num_orders}): −${m.shipping_total:,.2f}")
    out.append(f"   • Payment fees (7.5%): −${m.payment_fees:,.2f}")
    if m.ads_spend > 0:
        out.append(f"   • Ad spend (Meta + TikTok + Google): −${m.ads_spend:,.2f}")
    if settings.INCLUDI_COSTI_FISSI_IN_NET_PROFIT:
        out.append(f"   • Fixed-costs allocation: −${m.fixed_cost_daily:,.2f}")

    # ---- SEZIONE 3 — PER-PLATFORM AD BREAKDOWN ------------------------------
    be_roas = (breakeven or (None, None))[0]
    section3 = (
        _format_meta_section(meta_daily, meta_campaigns, be_roas)
        + _format_tiktok_section(tiktok_daily, tiktok_campaigns)
        + _format_google_section(google_daily)
        + _format_klaviyo_section(klaviyo_daily, klaviyo_campaigns)
    )
    if section3.strip():
        out.append("\n*3) AD PLATFORMS*")
        out.append(section3.rstrip("\n"))

    return "\n".join(out) + "\n"


def _format_google_section(google_daily: Optional[dict]) -> str:
    """Sezione Google Ads: totali account (USD). Nessun breakdown per campagna (per ora)."""
    if not google_daily:
        if settings.TRIPLEWHALE_API_KEY:
            return "\n🔎 *Google Ads*: data not available for this day.\n"
        return ""

    spend = float(google_daily.get("spend") or 0)
    revenue = float(google_daily.get("revenue") or 0)
    orders = int(google_daily.get("orders") or 0)
    roas = float(google_daily.get("roas") or 0)
    cpa = float(google_daily.get("cpa") or 0)

    out = (
        f"\n🔎 *Google Ads — {google_daily.get('day')}* _(USD, via Triple Whale)_\n"
        f"   • Spend: *${spend:,.2f}*\n"
        f"   • ROAS: *{roas:,.2f}x* (break-even {settings.BREAK_EVEN_ROAS:.2f}x)\n"
        f"   • Attributed revenue: ${revenue:,.2f}\n"
    )
    if orders > 0:
        out += f"   • CPA: ${cpa:,.2f} · conversions: {orders}\n"
    return out


def _format_tiktok_section(
    tiktok_daily: Optional[dict], tiktok_campaigns: Optional[list[dict]]
) -> str:
    """Sezione TikTok: totali + breakdown per campagna (USD)."""
    if not tiktok_daily:
        if settings.TRIPLEWHALE_API_KEY:
            return "\n🎵 *TikTok Ads*: data not available for this day.\n"
        return ""  # Triple Whale non configurato: nessuna sezione

    spend = float(tiktok_daily.get("spend") or 0)
    revenue = float(tiktok_daily.get("revenue") or 0)
    orders = int(tiktok_daily.get("orders") or 0)
    roas = float(tiktok_daily.get("roas") or 0)
    cpa = float(tiktok_daily.get("cpa") or 0)

    out = (
        f"\n🎵 *TikTok Ads — {tiktok_daily.get('day')}* _(USD, via Triple Whale)_\n"
        f"   • Spend: *${spend:,.2f}*\n"
        f"   • ROAS: *{roas:,.2f}x* (break-even {settings.BREAK_EVEN_ROAS:.2f}x)\n"
        f"   • Attributed revenue: ${revenue:,.2f}\n"
    )
    # CPA/conversioni solo se TikTok riporta gli ordini (altrimenti saltati)
    if orders > 0:
        out += f"   • CPA: ${cpa:,.2f} · conversions: {orders}\n"
    return out


def _format_meta_section(
    meta_daily: Optional[dict],
    meta_campaigns: Optional[list[dict]],
    be_roas: Optional[float] = None,
) -> str:
    """Sezione Meta: totali + breakdown per campagna (USD). `be_roas` = break-even 4gg."""
    if not meta_daily:
        if settings.META_ACCESS_TOKEN and settings.META_AD_ACCOUNT_ID:
            return "\n📣 *Meta Ads*: data not available for this day.\n"
        return ""  # Meta non configurato: nessuna sezione

    spend = float(meta_daily.get("spend") or 0)
    revenue = float(meta_daily.get("revenue") or 0)
    orders = int(meta_daily.get("orders") or 0)
    roas = float(meta_daily.get("roas") or 0)
    cpa = float(meta_daily.get("cpa") or 0)

    out = (
        f"\n📣 *Meta Ads — {meta_daily.get('day')}* _(USD)_\n"
        f"   • Spend: *${spend:,.2f}*\n"
        f"   • ROAS: *{roas:,.2f}x* (break-even {settings.BREAK_EVEN_ROAS:.2f}x)\n"
        f"   • CPA: ${cpa:,.2f}\n"
        f"   • Attributed revenue: ${revenue:,.2f} · purchases: {orders}\n"
    )

    campaigns = meta_campaigns or []
    if campaigns:
        be_str = f" (break-even {be_roas:,.2f}x)" if be_roas else ""
        out += "\n*Campaign breakdown* (top by spend):\n"
        for c in campaigns[:8]:
            name = (c.get("campaign_name") or "(no name)")[:34]
            c_spend = float(c.get("spend") or 0)
            c_rev = float(c.get("revenue") or 0)
            c_ord = int(c.get("orders") or 0)
            c_roas = float(c.get("roas") or 0)
            cpa_str = f"${(c_spend / c_ord):,.2f}" if c_ord > 0 else "n/a"
            out += (
                f"• *{name}* — spend ${c_spend:,.0f} · rev ${c_rev:,.0f} · "
                f"ord {c_ord} · ROAS {c_roas:,.2f}x · CPA {cpa_str}{be_str}\n"
            )
    return out


def _format_klaviyo_section(
    klaviyo_daily: Optional[dict], klaviyo_campaigns: Optional[list[dict]]
) -> str:
    """Sezione Klaviyo: revenue campagne + breakdown (USD). SOLO campagne, no flows."""
    if not klaviyo_daily:
        if settings.KLAVIYO_API_KEY:
            return "\n✉️ *Klaviyo (campaigns)*: data not available for this day.\n"
        return ""  # Klaviyo non configurato: nessuna sezione

    revenue = float(klaviyo_daily.get("revenue") or 0)
    opens = int(klaviyo_daily.get("opens") or 0)
    clicks = int(klaviyo_daily.get("clicks") or 0)
    conversions = int(klaviyo_daily.get("conversions") or 0)
    open_rate = float(klaviyo_daily.get("open_rate") or 0) * 100
    click_rate = float(klaviyo_daily.get("click_rate") or 0) * 100

    out = (
        f"\n✉️ *Klaviyo campaigns — {klaviyo_daily.get('day')}* _(USD, campaigns only)_\n"
        f"   • Attributed revenue: *${revenue:,.2f}*\n"
        f"   • Opens: {opens:,} ({open_rate:.1f}%) · Clicks: {clicks:,} ({click_rate:.1f}%)\n"
        f"   • Conversions: {conversions:,}\n"
    )

    campaigns = klaviyo_campaigns or []
    if campaigns:
        out += "\n*Campaign breakdown* (top by revenue):\n"
        for c in campaigns[:8]:
            name = (c.get("campaign_name") or "(no name)")[:34]
            c_rev = float(c.get("revenue") or 0)
            c_open = int(c.get("opens") or 0)
            c_click = int(c.get("clicks") or 0)
            c_conv = int(c.get("conversions") or 0)
            out += (
                f"• *{name}* — rev ${c_rev:,.0f} · "
                f"opens {c_open:,} · clicks {c_click:,} · conv {c_conv}\n"
            )
    return out


def build_monthly_pl(year: int, month: int) -> str:
    """
    P&L mensile DETERMINISTICO (codice puro, nessuna AI): aggrega le righe di
    daily_metrics del mese richiesto. Tutti i valori in USD.
    """
    from calendar import monthrange

    from src.db.supabase_client import SupabaseStore

    last_day = monthrange(year, month)[1]
    start = f"{year:04d}-{month:02d}-01"
    end = f"{year:04d}-{month:02d}-{last_day:02d}"

    store = SupabaseStore()
    rows = store.get_daily_metrics_range(start, end)
    if not rows:
        return f"📒 *P&L {year}-{month:02d}* — no data available for this month."

    def _s(key: str) -> float:
        return sum(float(r.get(key) or 0) for r in rows)

    revenue = _s("revenue")
    num_orders = int(_s("num_orders"))
    cogs = _s("cogs_total")
    shipping = _s("shipping_total")
    fees = _s("payment_fees")
    ads = _s("ads_spend")
    fixed = _s("fixed_cost_daily")
    op = _s("net_profit_operativo")
    net = _s("net_profit_netto")
    aov = (revenue / num_orders) if num_orders else 0.0

    return (
        f"📒 *P&L {year}-{month:02d}* _(USD, {len(rows)} days with data)_\n\n"
        f"🛒 Orders: *{num_orders}*\n"
        f"💰 Revenue: *${revenue:,.2f}*\n"
        f"🧾 AOV: ${aov:,.2f}\n\n"
        f"*Costs*\n"
        f"   • COGS: −${cogs:,.2f}\n"
        f"   • Shipping: −${shipping:,.2f}\n"
        f"   • Payment fees: −${fees:,.2f}\n"
        f"   • Ad spend: −${ads:,.2f}\n"
        f"   • Fixed costs: −${fixed:,.2f}\n\n"
        f"*Net profit for the month*\n"
        f"   • Operating: *${op:,.2f}*\n"
        f"   • Net: *${net:,.2f}*\n"
    )


if __name__ == "__main__":
    # Stampa a video il report di ieri (Shopify reale), senza Telegram.
    _, _text = build_daily_report(persist=False)
    print(_text)
