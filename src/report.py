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


def yesterday_window(now: Optional[datetime] = None) -> DayWindow:
    """Intervallo [00:00, 24:00) di IERI in Europe/Rome, tz-aware."""
    tz = pytz.timezone(settings.TIMEZONE)
    now = now.astimezone(tz) if now else datetime.now(tz)
    yesterday = (now - timedelta(days=1)).date()
    start = tz.localize(datetime.combine(yesterday, time.min))
    end = start + timedelta(days=1)
    return DayWindow(day_str=yesterday.isoformat(), start=start, end=end)


def build_daily_report(
    window: Optional[DayWindow] = None,
    persist: bool = True,
    force_meta: bool = False,
) -> tuple[DailyMetrics, str]:
    """Costruisce le metriche + il testo del report per la finestra indicata (default: ieri)."""
    window = window or yesterday_window()

    # Shopify: se fallisce/va in timeout, si degrada (0 ordini) e il report parte comunque.
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

    # Meta (Fase 2): una sola pull insights/giorno (cache su DB); spesa in USD.
    meta_daily, meta_campaigns, meta_spend = _load_meta(
        window.day_str, persist=persist, force=force_meta
    )

    # TikTok via Triple Whale (Fase 3): una sola pull Summary/giorno (cache su DB);
    # SOLO TikTok. La spesa è un costo reale e va sottratta dal net profit (come Meta).
    tiktok_daily, tiktok_campaigns, tiktok_spend = _load_tiktok(
        window.day_str, window.start.date().isoformat(), window.day_str, persist=persist
    )

    # Google Ads via Triple Whale (Fase 2): una sola pull Summary/giorno (cache su DB);
    # SOLO totali account. Anche questa spesa è un costo reale (sottratta dal net profit).
    google_daily, google_spend = _load_google(
        window.day_str, window.start.date().isoformat(), window.day_str, persist=persist
    )

    # Klaviyo (Fase 4): una sola pull reporting/giorno (cache su DB); SOLO campagne.
    # È revenue attribuita (informativa): NON entra nel net profit.
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

    if persist:
        _persist(orders, handle_map, metrics)

    return metrics, format_report(
        metrics, meta_daily, meta_campaigns, klaviyo_daily, klaviyo_campaigns,
        tiktok_daily, tiktok_campaigns, google_daily,
    )


def _load_meta(day: str, persist: bool, force: bool = False):
    """
    Restituisce (meta_daily_dict | None, meta_campaigns: list[dict], meta_spend_usd).

    Regola anti-ban: UNA pull insights al giorno. Se i dati del giorno sono già nel
    database, vengono riusati senza chiamare l'API Meta (i /report manuali non
    generano nuove chiamate). Se Meta non è configurato o fallisce, il report
    prosegue comunque in modalità solo-Shopify (meta_spend = 0).
    """
    if not (settings.META_ACCESS_TOKEN and settings.META_AD_ACCOUNT_ID):
        return None, [], 0.0

    store = None
    try:
        from src.db.supabase_client import SupabaseStore

        store = SupabaseStore()
    except Exception as exc:  # noqa: BLE001
        print(f"[report] Supabase non disponibile per Meta: {exc}")

    # 1) prova la cache su DB (evita una nuova chiamata API)
    if store is not None and not force:
        cached = store.get_meta_daily_for_day(day)
        if cached:
            campaigns = store.get_meta_campaigns_for_day(day)
            return cached, campaigns, float(cached.get("spend") or 0.0)

    # 2) nessuna cache: UNA pull insights dal connettore Meta
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


def _load_tiktok(day: str, start: str, end: str, persist: bool, force: bool = False):
    """
    Restituisce (tiktok_daily_dict | None, tiktok_campaigns: list[dict], tiktok_spend_usd).

    Stessa regola di Meta/Klaviyo: UNA pull Summary al giorno, con cache su DB. Estrae
    SOLO TikTok. Se Triple Whale non è configurato o fallisce, il report prosegue
    comunque (tiktok_spend = 0). `start`/`end` sono date YYYY-MM-DD.
    """
    if not settings.TRIPLEWHALE_API_KEY:
        return None, [], 0.0

    store = None
    try:
        from src.db.supabase_client import SupabaseStore

        store = SupabaseStore()
    except Exception as exc:  # noqa: BLE001
        print(f"[report] Supabase non disponibile per TikTok: {exc}")

    # 1) cache su DB (evita una nuova chiamata API)
    if store is not None and not force:
        cached = store.get_tiktok_daily_for_day(day)
        if cached:
            campaigns = store.get_tiktok_campaigns_for_day(day)
            return cached, campaigns, float(cached.get("spend") or 0.0)

    # 2) nessuna cache: UNA pull Summary + estrazione SOLO TikTok
    try:
        from src.connectors.triplewhale import TripleWhaleConnector, extract_tiktok
        from src.metrics.tiktok import compute_tiktok_metrics

        tw = TripleWhaleConnector()
        summary = tw.get_summary(start, end)
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
        print(f"[report] pull TikTok (Triple Whale) saltata: {exc}")
        return None, [], 0.0


def _load_google(day: str, start: str, end: str, persist: bool, force: bool = False):
    """
    Restituisce (google_daily_dict | None, google_spend_usd).

    Stessa regola di TikTok: UNA pull Summary al giorno, con cache su DB. Estrae SOLO
    i totali Google (no per-campaign). Se Triple Whale non è configurato o fallisce,
    il report prosegue (google_spend = 0). `start`/`end` sono date YYYY-MM-DD.
    """
    if not settings.TRIPLEWHALE_API_KEY:
        return None, 0.0

    store = None
    try:
        from src.db.supabase_client import SupabaseStore

        store = SupabaseStore()
    except Exception as exc:  # noqa: BLE001
        print(f"[report] Supabase non disponibile per Google: {exc}")

    # 1) cache su DB (evita una nuova chiamata API)
    if store is not None and not force:
        cached = store.get_google_daily_for_day(day)
        if cached:
            return cached, float(cached.get("spend") or 0.0)

    # 2) nessuna cache: UNA pull Summary + estrazione SOLO Google
    try:
        from src.connectors.triplewhale import TripleWhaleConnector, extract_google
        from src.metrics.google import compute_google_metrics

        tw = TripleWhaleConnector()
        summary = tw.get_summary(start, end)
        google = extract_google(summary)
        if not google:
            print("[report] nessuna metrica Google trovata nel Summary di Triple Whale.")
            return None, 0.0

        computed = compute_google_metrics(day, google)
        if persist and store is not None:
            store.upsert_google_daily(computed)
        return computed.as_db_row(), computed.spend
    except Exception as exc:  # noqa: BLE001 — il report deve arrivare comunque
        print(f"[report] pull Google (Triple Whale) saltata: {exc}")
        return None, 0.0


def _load_klaviyo(day: str, start_iso: str, end_iso: str, persist: bool, force: bool = False):
    """
    Restituisce (klaviyo_daily_dict | None, klaviyo_campaigns: list[dict]).

    SOLO CAMPAGNE (no flows). Stessa regola di Meta: UNA pull reporting al giorno,
    con cache su DB. Se Klaviyo non è configurato o fallisce, il report prosegue
    comunque senza la sezione Klaviyo.
    """
    if not settings.KLAVIYO_API_KEY:
        return None, []

    store = None
    try:
        from src.db.supabase_client import SupabaseStore

        store = SupabaseStore()
    except Exception as exc:  # noqa: BLE001
        print(f"[report] Supabase non disponibile per Klaviyo: {exc}")

    # 1) cache su DB (evita una nuova chiamata API)
    if store is not None and not force:
        cached = store.get_klaviyo_daily_for_day(day)
        if cached:
            return cached, store.get_klaviyo_campaigns_for_day(day)

    # 2) nessuna cache: UNA pull reporting (campaign-values-report)
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


def format_report(
    m: DailyMetrics,
    meta_daily: Optional[dict] = None,
    meta_campaigns: Optional[list[dict]] = None,
    klaviyo_daily: Optional[dict] = None,
    klaviyo_campaigns: Optional[list[dict]] = None,
    tiktok_daily: Optional[dict] = None,
    tiktok_campaigns: Optional[list[dict]] = None,
    google_daily: Optional[dict] = None,
) -> str:
    """Formatta il report Telegram (Markdown). Tutti i valori in USD."""
    fixed_line = ""
    if settings.INCLUDI_COSTI_FISSI_IN_NET_PROFIT:
        fixed_line = f"   • Fixed-costs allocation: −${m.fixed_cost_daily:,.2f}\n"

    ads_line = ""
    if m.ads_spend > 0:
        ads_line = f"   • Ad spend (Meta + TikTok + Google): −${m.ads_spend:,.2f}\n"

    report = (
        f"📊 *Daily report — {m.day}*\n"
        f"_(currency: USD)_\n\n"
        f"🛒 Orders: *{m.num_orders}*\n"
        f"💰 Revenue: *${m.revenue:,.2f}*\n"
        f"🧾 AOV: ${m.aov:,.2f}\n\n"
        f"*Costs for the day*\n"
        f"   • Product COGS: −${m.cogs_total:,.2f}\n"
        f"   • Shipping ($7 × {m.num_orders}): −${m.shipping_total:,.2f}\n"
        f"   • Payment fees (7.5%): −${m.payment_fees:,.2f}\n"
        f"{ads_line}"
        f"{fixed_line}\n"
        f"*Net profit*\n"
        f"   • Operating (excl. fixed costs): *${m.net_profit_operativo:,.2f}*\n"
        f"   • Net (incl. fixed costs): *${m.net_profit_netto:,.2f}*\n"
    )
    report += _format_meta_section(meta_daily, meta_campaigns)
    report += _format_tiktok_section(tiktok_daily, tiktok_campaigns)
    report += _format_google_section(google_daily)
    report += _format_klaviyo_section(klaviyo_daily, klaviyo_campaigns)
    return report


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
    meta_daily: Optional[dict], meta_campaigns: Optional[list[dict]]
) -> str:
    """Sezione Meta: totali + breakdown per campagna (USD)."""
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
        out += "\n*Campaign breakdown* (top by spend):\n"
        for c in campaigns[:8]:
            name = (c.get("campaign_name") or "(no name)")[:34]
            c_spend = float(c.get("spend") or 0)
            c_rev = float(c.get("revenue") or 0)
            c_ord = int(c.get("orders") or 0)
            c_cvr = float(c.get("cvr") or 0) * 100
            out += (
                f"• *{name}* — spend ${c_spend:,.0f} · "
                f"rev ${c_rev:,.0f} · ord {c_ord} · CVR {c_cvr:.1f}%\n"
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
