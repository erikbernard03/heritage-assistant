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
from datetime import date, datetime, time, timedelta
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


def _orders_in_window(orders: list[dict], window: DayWindow) -> list[dict]:
    """
    Tiene SOLO gli ordini con created_at nel semiaperto [start, end) di Roma.

    Shopify filtra `created_at_max` in modo INCLUSIVO: un ordine creato ESATTAMENTE alla
    mezzanotte di confine tornerebbe sia nel giorno che nel successivo (doppio conteggio al
    boundary). Qui rifiltriamo lato nostro con semantica [start, end) così ogni ordine cade in
    UN solo giorno Europe/Rome. Ordini senza created_at valido vengono tenuti (best-effort).
    """
    tz = pytz.timezone(settings.TIMEZONE)
    kept: list[dict] = []
    for o in orders:
        raw = o.get("created_at")
        if not raw:
            kept.append(o)
            continue
        try:
            created = datetime.fromisoformat(str(raw).replace("Z", "+00:00")).astimezone(tz)
        except (ValueError, TypeError):
            kept.append(o)
            continue
        if window.start <= created < window.end:
            kept.append(o)
    return kept


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


@dataclass
class GatheredDay:
    """Tutti i dati LIVE di un giorno (metriche + piattaforme), pronti per il rendering."""
    metrics: DailyMetrics
    meta_daily: Optional[dict]
    meta_campaigns: list
    tiktok_daily: Optional[dict]
    tiktok_campaigns: list
    google_daily: Optional[dict]
    klaviyo_daily: Optional[dict]
    klaviyo_campaigns: list


def _gather_day(window: DayWindow, persist: bool) -> GatheredDay:
    """
    Esegue TUTTe le pull live per la finestra e calcola le metriche deterministiche.
    Condiviso da build_daily_report e dagli snapshot /today e /yesterday (stessi numeri).
    """
    # Shopify: se fallisce/va in timeout, si degrada (0 ordini) e il report parte comunque.
    shop = None
    try:
        shop = ShopifyConnector()
        orders = shop.get_orders(window.start, window.end)
        handle_map = shop.get_products_handle_map()
    except Exception as exc:  # noqa: BLE001 — il report deve arrivare comunque
        print(f"[report] Shopify pull failed: {exc}")
        orders, handle_map = [], {}

    # Boundary fix: tieni solo gli ordini davvero nel giorno [start, end) di Roma (Shopify usa
    # created_at_max INCLUSIVO -> un ordine a mezzanotte di confine finirebbe in due giorni).
    orders = _orders_in_window(orders, window)
    # annota il giorno Europe/Rome su ogni ordine (per la persistenza)
    for o in orders:
        o["_day_rome"] = window.day_str

    # UNA sola pull del Summary Triple Whale per run: lo stesso oggetto dict alimenta
    # TikTok, Google e la CVR di negozio. Il todayHour è impostato all'ora corrente Rome
    # dal connettore, quindi per OGGI i valori sono "finora".
    tw_summary = _fetch_tw_summary(window.start.date().isoformat(), window.day_str)

    # Meta (Fase 2): insights del giorno via bucketing ORARIO→Roma (per OGGI = ore finora).
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
    # Visitatori reali (sessioni Shopify): None se scope read_reports mancante -> stima in dashboard
    metrics.store_sessions = _load_shopify_sessions(shop, window.day_str)

    if persist:
        _persist(orders, handle_map, metrics, tw_summary=tw_summary)

    return GatheredDay(
        metrics=metrics, meta_daily=meta_daily, meta_campaigns=meta_campaigns,
        tiktok_daily=tiktok_daily, tiktok_campaigns=tiktok_campaigns,
        google_daily=google_daily, klaviyo_daily=klaviyo_daily,
        klaviyo_campaigns=klaviyo_campaigns,
    )


def build_daily_report(
    window: Optional[DayWindow] = None,
    persist: bool = True,
) -> tuple[DailyMetrics, str]:
    """Costruisce le metriche + il testo del report per la finestra indicata (default: ieri)."""
    window = window or yesterday_window()
    g = _gather_day(window, persist=persist)

    # Break-even ROAS/CPA dalla media dei 4 giorni PRECEDENTI (da daily_metrics).
    breakeven = _load_breakeven(window.day_str)

    return g.metrics, format_report(
        g.metrics, g.meta_daily, g.meta_campaigns, g.klaviyo_daily, g.klaviyo_campaigns,
        g.tiktok_daily, g.tiktok_campaigns, g.google_daily, breakeven=breakeven,
    )


def _own_day_breakeven(m: DailyMetrics):
    """Break-even (dict contribution+profit) dai NUMERI DEL GIORNO STESSO (non finestra 4gg).
    Valori None se 0 ordini -> la vista mostra 'n/a'."""
    from src.metrics.profit import compute_breakeven_full

    return compute_breakeven_full([{
        "revenue": m.revenue, "num_orders": m.num_orders, "cogs_total": m.cogs_total,
        "day": m.day,
    }])


def build_today_snapshot(now: Optional[datetime] = None) -> str:
    """
    Snapshot INTRADAY di OGGI (mezzanotte Roma → ora), tirato LIVE, mai da cache.
    NON persiste (dato parziale: non deve inquinare daily_metrics). Break-even dai
    numeri di oggi. Attribuzione ads provvisoria (revenue/ROAS si assestano dopo).
    """
    tz = pytz.timezone(settings.TIMEZONE)
    now = now.astimezone(tz) if now else datetime.now(tz)
    today = now.date()
    g = _gather_day(day_window(today), persist=False)
    header = (
        f"📊 *Today so far — {today.isoformat()} {now.strftime('%H:%M')} Rome* _(USD)_"
    )
    return format_snapshot(g, _own_day_breakeven(g.metrics), header, provisional=True)


def build_yesterday_snapshot(now: Optional[datetime] = None) -> str:
    """
    Snapshot del giorno di IERI COMPLETO (mezzanotte→mezzanotte), tirato LIVE ad ogni
    chiamata. Persiste (giorno canonico completo: refresh utile). Break-even dai numeri
    di ieri stesso.
    """
    tz = pytz.timezone(settings.TIMEZONE)
    now = now.astimezone(tz) if now else datetime.now(tz)
    yday = (now - timedelta(days=1)).date()
    g = _gather_day(day_window(yday), persist=True)
    header = f"📊 *Yesterday — {yday.isoformat()}* _(USD)_"
    return format_snapshot(g, _own_day_breakeven(g.metrics), header, provisional=False)


def format_snapshot(
    g: "GatheredDay", breakeven, header: str, provisional: bool = False
) -> str:
    """
    Versione COMPATTA del layout a 3 sezioni per gli snapshot /today e /yesterday.
    Break-even dai numeri del giorno stesso (passato in `breakeven`).
    """
    m = g.metrics
    be = breakeven or {}
    cogs_po = (m.cogs_total / m.num_orders) if m.num_orders else 0.0
    gross = m.revenue - m.cogs_total

    out: list[str] = [header]

    # ---- 1) KEY METRICS (compatto) ----
    out.append("\n*1) KEY METRICS*")
    out.append(f"💰 Revenue: *${m.revenue:,.2f}* · 🛒 Orders: *{m.num_orders}*")
    out.append(f"🧾 AOV: ${m.aov:,.2f}")
    out.append(f"🏷️ COGS: ${m.cogs_total:,.2f} (${cogs_po:,.2f}/order)")
    out.append(f"📦 Gross profit (rev − COGS): *${gross:,.2f}*")
    out.append(
        f"💵 Net profit — operating *${m.net_profit_operativo:,.2f}* · "
        f"net *${m.net_profit_netto:,.2f}*"
    )
    c_roas = f"{be['roas']:,.2f}x" if be.get("roas") else "n/a"
    c_cpa = f"${be['cpa']:,.2f}" if be.get("cpa") is not None else "n/a"
    p_roas = f"{be['profit_roas']:,.2f}x" if be.get("profit_roas") else "n/a"
    p_cpa = f"${be['profit_cpa']:,.2f}" if be.get("profit_cpa") is not None else "n/a"
    out.append(f"⚖️ Contribution break-even ROAS: {c_roas} · CPA: {c_cpa} (own day)")
    out.append(f"🎯 Profit break-even ROAS: {p_roas} · CPA: {p_cpa} (incl. fixed; volume-dependent)")
    prov = " · provisional" if provisional else ""
    for line in (
        _roas_cpa_line("📣", "Meta", g.meta_daily),
        _roas_cpa_line("🎵", "TikTok", g.tiktok_daily),
        _roas_cpa_line("🔎", "Google", g.google_daily),
    ):
        if line:
            out.append(line + prov)
    if g.klaviyo_daily:
        kla_rev = float(g.klaviyo_daily.get("revenue") or 0)
        out.append(f"✉️ Klaviyo campaign revenue: ${kla_rev:,.2f}")

    # ---- 2) COST BREAKDOWN ----
    out.append("\n*2) COST BREAKDOWN*")
    out.append(f"   • Product COGS: −${m.cogs_total:,.2f}")
    out.append(f"   • Shipping cost ($7 × {m.num_orders}): −${m.shipping_total:,.2f}")
    out.append(f"   • Payment fees (7.5%): −${m.payment_fees:,.2f}")
    if m.ads_spend > 0:
        out.append(f"   • Ad spend (Meta + TikTok + Google): −${m.ads_spend:,.2f}")
    if settings.INCLUDI_COSTI_FISSI_IN_NET_PROFIT:
        out.append(f"   • Fixed-costs allocation (full day): −${m.fixed_cost_daily:,.2f}")

    # ---- 3) AD PLATFORMS (compatto: una riga per piattaforma) ----
    out.append("\n*3) AD PLATFORMS*")
    plat: list[str] = []
    for emoji, name, d in (
        ("📣", "Meta", g.meta_daily),
        ("🎵", "TikTok", g.tiktok_daily),
        ("🔎", "Google", g.google_daily),
    ):
        if d:
            plat.append(
                f"{emoji} {name} — spend ${float(d.get('spend') or 0):,.2f} · "
                f"rev ${float(d.get('revenue') or 0):,.2f} · "
                f"ROAS {float(d.get('roas') or 0):,.2f}x · "
                f"{int(d.get('orders') or 0)} purch"
            )
    out.append("\n".join(plat) if plat else "_No ad-platform data yet._")

    if provisional:
        out.append(
            "\n_Note: today's ad attribution is provisional — revenue/ROAS settle later._"
        )
    return "\n".join(out) + "\n"


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


def _load_shopify_sessions(shop, day: str) -> Optional[int]:
    """Sessioni/visitatori reali Shopify per il giorno; None se scope mancante/non disp."""
    if shop is None:
        return None
    try:
        return shop.get_sessions(day)
    except Exception as exc:  # noqa: BLE001
        print(f"[report] Shopify sessions non disponibili: {exc}")
        return None


def _load_breakeven(day: str, store=None):
    """
    Break-even (dict contribution+profit) dai 4 giorni REALI più recenti PRIMA di `day`.
    Robusto ai buchi: se mancano giorni di calendario, va più indietro fino a
    raccogliere 4 giorni che hanno effettivamente dati in daily_metrics.
    """
    try:
        from src.metrics.profit import compute_breakeven_full

        if store is None:
            from src.db.supabase_client import SupabaseStore

            store = SupabaseStore()
        rows = store.get_daily_metrics_before(day, limit=4)
        return compute_breakeven_full(rows)
    except Exception as exc:  # noqa: BLE001 — il report deve arrivare comunque
        print(f"[report] break-even non calcolato: {exc}")
        return None


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
        raw = _meta_rows_for_rome_day(meta, day)
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


def meta_bucketing_status(meta=None) -> dict:
    """
    Stato del bucketing Meta per la diagnostica (/meta_check): prova a leggere il fuso
    dell'account e a fare una piccola pull ORARIA; riporta la modalità attiva.
    Ritorna {mode, account_tz, target_tz, detail}. Nessuna scrittura.
    """
    from src.connectors.meta import MetaConnector

    if meta is None:
        meta = MetaConnector()
    target_tz = settings.TIMEZONE
    try:
        account_tz = meta.get_account_timezone_name()
    except Exception as exc:  # noqa: BLE001
        return {"mode": "unknown", "account_tz": None, "target_tz": target_tz,
                "detail": f"timezone_name non leggibile: {exc}"}

    tz = pytz.timezone(target_tz)
    y = (datetime.now(tz).date() - timedelta(days=1)).isoformat()
    try:
        # piccola sonda oraria su ieri (±1 giorno account) per capire se il breakdown c'è
        since = (date.fromisoformat(y) - timedelta(days=1)).isoformat()
        until = (date.fromisoformat(y) + timedelta(days=1)).isoformat()
        rows = meta.get_hourly_campaign_insights(since, until)
        has_hourly = any(meta.HOURLY_BREAKDOWN in r for r in rows)
        if has_hourly:
            return {"mode": "hourly→Rome", "account_tz": account_tz, "target_tz": target_tz,
                    "detail": f"{len(rows)} righe orarie; ri-bucketate in giorni {target_tz}"}
        return {"mode": "daily (account tz)", "account_tz": account_tz, "target_tz": target_tz,
                "detail": "breakdown orario assente nelle righe -> fallback giornaliero"}
    except Exception as exc:  # noqa: BLE001
        return {"mode": "daily (account tz)", "account_tz": account_tz, "target_tz": target_tz,
                "detail": f"pull oraria fallita -> fallback giornaliero: {exc}"}


def _meta_rows_for_rome_day(meta, day: str) -> list[dict]:
    """
    Righe Meta (per campagna) per il giorno di calendario Europe/Rome `day`.

    DEFAULT: breakdown ORARIO nel fuso dell'account (letto dinamicamente, es. Asia/Dubai)
    ri-bucketato nei giorni di Roma (mezzanotte-mezzanotte di Roma, come Shopify). Si tira
    UN GIORNO ACCOUNT IN PIÙ per lato ([day-1, day+1]) così nessuna ora va persa ai bordi.

    FALLBACK graceful: se la pull oraria fallisce o il breakdown non è disponibile, si
    ripiega sulla pull GIORNALIERA attuale (giorni del fuso account) e si logga un warning.

    NB: con il re-bucketing i numeri differiranno LEGGERMENTE dalla vista giornaliera di
    Ads Manager (che usa i giorni del fuso account, es. Dubai). È voluto e corretto: qui i
    giorni Meta coincidono con quelli di Shopify/Europe/Rome.
    """
    from src.metrics.meta import rebucket_hourly_to_daily_rows

    # ±1 giorno account attorno al giorno di Roma, per non perdere ore ai bordi.
    d = date.fromisoformat(day)
    since = (d - timedelta(days=1)).isoformat()
    until = (d + timedelta(days=1)).isoformat()

    try:
        account_tz = meta.get_account_timezone_name()
        hourly = meta.get_hourly_campaign_insights(since, until)
        if hourly and any(meta.HOURLY_BREAKDOWN in r for r in hourly):
            by_rome_day = rebucket_hourly_to_daily_rows(hourly, account_tz, settings.TIMEZONE)
            print(
                f"[report] Meta bucketing ORARIO→{settings.TIMEZONE}: account_tz={account_tz} "
                f"· {len(hourly)} righe orarie [{since}→{until}] → giorno {day} "
                f"({len(by_rome_day.get(day, []))} campagne)"
            )
            return by_rome_day.get(day, [])
        print(
            f"[report] ⚠️ Meta: breakdown orario non disponibile per [{since}→{until}] "
            f"-> fallback pull GIORNALIERA (giorni fuso account) per {day}."
        )
    except Exception as exc:  # noqa: BLE001 — degrada al giornaliero
        print(f"[report] ⚠️ Meta pull oraria fallita ({exc}) -> fallback giornaliero per {day}.")

    return meta.get_daily_campaign_insights(day)


def refresh_meta_range(
    start_iso: str, end_iso: str, max_days: int = 60, meta=None, store=None
) -> list[tuple]:
    """
    Ri-bucketizza Meta per OGNI giorno (Europe/Rome) in [start_iso, end_iso] e
    sovrascrive meta_daily + meta_campaigns. Serve a correggere lo storico dopo il
    passaggio al bucketing ORARIO→Roma (i giorni Meta ora coincidono con Shopify/Roma).

    EFFICIENTE: una SOLA pull oraria per l'intero intervallo (± 1 giorno account per lato,
    così nessuna ora va persa ai bordi), poi ri-bucketing in giorni di Roma e upsert per
    ogni giorno. Se la pull oraria fallisce o il breakdown non è disponibile, ripiega
    per-giorno sul percorso standard (_load_meta), che ha il proprio fallback giornaliero.

    Ritorna una lista (giorno, spesa_usd|'ERR', ordini|messaggio, modalità) per il riepilogo.

    NB: i numeri differiranno LEGGERMENTE da Ads Manager (giorni fuso account, es. Dubai):
    è voluto — qui i giorni Meta sono allineati a Europe/Rome.
    """
    from src.connectors.meta import MetaConnector
    from src.db.supabase_client import SupabaseStore
    from src.metrics.meta import compute_meta_metrics, rebucket_hourly_to_daily_rows

    if not (settings.META_ACCESS_TOKEN and settings.META_AD_ACCOUNT_ID):
        raise RuntimeError("Meta non configurato (META_ACCESS_TOKEN / META_AD_ACCOUNT_ID).")

    d0 = date.fromisoformat(start_iso)
    d1 = date.fromisoformat(end_iso)
    if d1 < d0:
        d0, d1 = d1, d0
    if (d1 - d0).days + 1 > max_days:
        raise ValueError(f"Range troppo ampio (> {max_days} giorni).")

    meta = meta or MetaConnector()
    store = store or SupabaseStore()
    currency = meta.get_account_currency()
    days = [(d0 + timedelta(days=i)).isoformat() for i in range((d1 - d0).days + 1)]

    # 1) tentativo BULK: una pull oraria per tutto l'intervallo (± 1 giorno account).
    by_rome_day: Optional[dict] = None
    try:
        account_tz = meta.get_account_timezone_name()
        since = (d0 - timedelta(days=1)).isoformat()
        until = (d1 + timedelta(days=1)).isoformat()
        hourly = meta.get_hourly_campaign_insights(since, until)
        if hourly and any(meta.HOURLY_BREAKDOWN in r for r in hourly):
            by_rome_day = rebucket_hourly_to_daily_rows(hourly, account_tz, settings.TIMEZONE)
            print(
                f"[refresh_meta] ORARIO→{settings.TIMEZONE}: account_tz={account_tz} · "
                f"{len(hourly)} righe orarie [{since}→{until}] → {len(days)} giorni Roma."
            )
        else:
            print("[refresh_meta] ⚠️ breakdown orario assente -> fallback per-giorno (_load_meta).")
    except Exception as exc:  # noqa: BLE001 — degrada al per-giorno
        print(f"[refresh_meta] ⚠️ pull oraria bulk fallita ({exc}) -> fallback per-giorno.")

    out: list[tuple] = []
    for day in days:
        try:
            if by_rome_day is not None:
                rows = by_rome_day.get(day, [])
                computed = compute_meta_metrics(day, rows, account_currency=currency)
                store.upsert_meta_daily(computed)
                store.upsert_meta_campaigns(computed)
                out.append((day, round(computed.spend, 2), computed.orders, "hourly→Rome"))
            else:
                # fallback: percorso standard per-giorno (ri-bucketing orario con proprio fallback)
                daily_dict, _camps, spend = _load_meta(day, persist=True)
                orders = int((daily_dict or {}).get("orders") or 0)
                out.append((day, round(spend, 2), orders, "per-day"))
        except Exception as exc:  # noqa: BLE001
            out.append((day, "ERR", str(exc)[:80], "-"))
    return out


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
        from src.metrics.klaviyo import compute_flow_revenue, compute_klaviyo_metrics

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

        # FLOWS: snapshot notturno della revenue flows del giorno (best-effort: se lo scope
        # Flows:Read manca o l'endpoint fallisce, flow_revenue resta None e il report va lo stesso).
        try:
            flow_raw = kc.get_flow_report(start_iso, end_iso, metric_id)
            computed.flow_revenue, _flows = compute_flow_revenue(flow_raw)
        except Exception as exc:  # noqa: BLE001
            print(f"[report] pull Klaviyo FLOWS saltata: {exc}")

        if persist and store is not None:
            store.upsert_klaviyo_daily(computed)
            store.upsert_klaviyo_campaigns(computed)

        return computed.as_db_row(), [c.as_db_row() for c in computed.campaigns]
    except Exception as exc:  # noqa: BLE001 — il report deve arrivare comunque
        print(f"[report] pull Klaviyo saltata: {exc}")
        return None, []


def load_klaviyo_period(start_day: str, end_day: str) -> dict:
    """
    Revenue Klaviyo di PERIODO (CAMPAGNE + FLOWS) via query a finestra piena sull'intervallo
    [start_day, end_day] (giorni Europe/Rome inclusi). UNA query per report-type: ogni
    campagna/flow è attribuito UNA volta sull'intera finestra di attribuzione.

    Perché serve: sommare gli snapshot GIORNALIERI SOTTOSTIMA (giorni-buco + attribuzione
    tardiva). La finestra piena combacia con la dashboard Klaviyo.

    Ritorna SEMPRE un dict (mai eccezione), così l'errore è VISIBILE e il chiamante può
    ripiegare sui valori aggregati dal DB:
      {ok, error, campaigns_revenue, flows_revenue, campaigns:[...], flows:[...],
       daily: klaviyo_daily_dict|None}
    - ok=False + error valorizzato se la query fallisce o Klaviyo non è configurato.
    - flows_revenue=None se lo scope Flows:Read manca / l'endpoint flow fallisce.
    """
    out = {"ok": False, "error": None, "campaigns_revenue": None, "flows_revenue": None,
           "campaigns": [], "flows": [], "daily": None}
    if not settings.KLAVIYO_API_KEY:
        out["error"] = "KLAVIYO_API_KEY non impostata su questo servizio."
        return out
    try:
        from src.connectors.klaviyo import KlaviyoConnector
        from src.metrics.klaviyo import compute_flow_revenue, compute_klaviyo_metrics

        tz = pytz.timezone(settings.TIMEZONE)
        s = tz.localize(datetime.combine(date.fromisoformat(start_day), time.min))
        # end ESCLUSIVO: mezzanotte del giorno DOPO end_day, così l'ultimo giorno è incluso.
        e = tz.localize(datetime.combine(date.fromisoformat(end_day) + timedelta(days=1), time.min))

        kc = KlaviyoConnector()
        metric_id = kc.resolve_conversion_metric_id()

        # CAMPAGNE (finestra piena)
        raw = kc.get_daily_campaign_report(s.isoformat(), e.isoformat(), metric_id)
        ids = [str((r.get("groupings") or {}).get("campaign_id") or "")
               for r in raw if (r.get("groupings") or {}).get("campaign_id")]
        names = kc.get_campaign_names(ids)
        computed = compute_klaviyo_metrics(f"{start_day} → {end_day}", raw, names=names)
        out["campaigns"] = [c.as_db_row() for c in computed.campaigns]
        out["campaigns_revenue"] = round(computed.revenue, 2)

        # FLOWS (finestra piena) — best-effort: scope Flows:Read separato
        try:
            flow_raw = kc.get_flow_report(s.isoformat(), e.isoformat(), metric_id)
            fids = [str((r.get("groupings") or {}).get("flow_id") or "")
                    for r in flow_raw if (r.get("groupings") or {}).get("flow_id")]
            fnames = kc.get_flow_names(fids)
            flows_total, flows = compute_flow_revenue(flow_raw, names=fnames)
            computed.flow_revenue = flows_total
            out["flows"] = flows
            out["flows_revenue"] = round(flows_total, 2)
        except Exception as exc:  # noqa: BLE001
            out["error"] = f"flows: {exc}"   # campagne ok, flows no (scope?)
            print(f"[report] Klaviyo FLOWS di periodo saltati: {exc}")

        out["daily"] = computed.as_db_row()
        out["ok"] = True
        return out
    except Exception as exc:  # noqa: BLE001 — NON inghiottire: l'errore torna al chiamante
        out["error"] = str(exc)[:300]
        print(f"[report] pull Klaviyo di periodo FALLITA: {exc}")
        return out


def _persist_product_units(store, metrics: DailyMetrics) -> None:
    """Classifica i line item del giorno e salva le unità per prodotto (best-effort)."""
    try:
        from src.metrics.product_units import units_by_key_from_line_items

        units = units_by_key_from_line_items(metrics.line_items)
        store.upsert_product_units(metrics.day, units)
    except Exception as exc:  # noqa: BLE001 — non bloccare il salvataggio principale
        print(f"[report] ⚠️ product_units non salvate per {metrics.day}: {exc}")


def _persist_sales_by_country(store, day: str, orders: list[dict]) -> None:
    """Calcola le vendite per paese del giorno e le salva (best-effort)."""
    try:
        from src.metrics.sales_location import revenue_by_country

        store.upsert_sales_by_country(day, revenue_by_country(orders))
    except Exception as exc:  # noqa: BLE001 — non bloccare il salvataggio principale
        print(f"[report] ⚠️ sales_by_country non salvate per {day}: {exc}")


def _persist_sales_by_hour(store, day: str, orders: list[dict]) -> None:
    """Calcola le vendite per ORA (Europe/Rome) del giorno e le salva (best-effort)."""
    try:
        from src.metrics.sales_timing import revenue_by_hour

        store.upsert_sales_by_hour(day, revenue_by_hour(orders))
    except Exception as exc:  # noqa: BLE001 — non bloccare il salvataggio principale
        print(f"[report] ⚠️ sales_by_hour non salvate per {day}: {exc}")


def _persist_sales_by_source(store, day: str, orders: list[dict]) -> None:
    """Classifica gli ordini per sorgente last-click e salva (best-effort)."""
    try:
        from src.metrics.sales_source import revenue_by_source

        store.upsert_orders_by_source(day, revenue_by_source(orders))
    except Exception as exc:  # noqa: BLE001 — non bloccare il salvataggio principale
        print(f"[report] ⚠️ orders_by_source non salvate per {day}: {exc}")


def _persist_tw_pixel(store, day: str, tw_summary: Optional[dict]) -> None:
    """Salva l'attribuzione pixel Triple Whale per canale del giorno (best-effort)."""
    if not tw_summary:
        return
    try:
        from src.connectors.triplewhale import extract_pixel_attribution

        store.upsert_tw_pixel_daily(day, extract_pixel_attribution(tw_summary))
    except Exception as exc:  # noqa: BLE001 — non bloccare il salvataggio principale
        print(f"[report] ⚠️ tw_pixel non salvato per {day}: {exc}")


def _persist_refunds(store, day: str, orders: list[dict]) -> None:
    """Salva i refund Shopify del giorno (visibilità; best-effort)."""
    try:
        from src.metrics.stripe_metrics import refunds_from_orders

        agg = refunds_from_orders(orders)
        store.upsert_refunds_daily(day, agg.get(day, {"amount": 0.0, "count": 0}))
    except Exception as exc:  # noqa: BLE001 — non bloccare il salvataggio principale
        print(f"[report] ⚠️ refunds non salvati per {day}: {exc}")


def _persist_stripe(store, day: str) -> None:
    """
    Pull Stripe del giorno (SOLA LETTURA, best-effort): stripe_daily[day] dalle balance
    transactions + refresh payout (ultimi 35g) e dispute (ultimi 120g). No-op se non configurato.
    """
    if not settings.STRIPE_API_KEY:
        return
    try:
        from src.connectors.stripe_conn import StripeConnector
        from src.metrics.stripe_metrics import (
            convert_payouts_usd,
            daily_from_balance_transactions,
            settlement_to_usd_rate,
        )

        d = date.fromisoformat(day)
        sc = StripeConnector()
        # Finestra 35g: serve sia per il tasso di cambio effettivo sia per i payout.
        txns = sc.balance_transactions(d - timedelta(days=35), d)
        agg = daily_from_balance_transactions(txns)
        store.upsert_stripe_daily(day, agg.get(day, {}))
        rate = settlement_to_usd_rate(txns)
        payouts = convert_payouts_usd(sc.payouts(d - timedelta(days=35), d + timedelta(days=7)), rate)
        store.upsert_stripe_payouts(payouts)
        store.upsert_stripe_disputes(sc.disputes(d - timedelta(days=120), d))
    except Exception as exc:  # noqa: BLE001
        print(f"[report] ⚠️ Stripe sync saltato per {day}: {exc}")


def backfill_stripe_range(start_iso: str, end_iso: str, max_days: int = 400, store=None) -> dict:
    """
    Riempie stripe_daily per OGNI giorno in [start, end] (una pull balance-transactions per
    giorno) + payout/dispute dell'intero range (una volta). Ritorna un riepilogo.
    """
    from src.connectors.stripe_conn import StripeConnector
    from src.db.supabase_client import SupabaseStore
    from src.metrics.stripe_metrics import (
        convert_payouts_usd,
        daily_from_balance_transactions,
        settlement_to_usd_rate,
    )

    if not settings.STRIPE_API_KEY:
        raise RuntimeError("STRIPE_API_KEY non configurata.")
    d0 = date.fromisoformat(start_iso)
    d1 = date.fromisoformat(end_iso)
    if d1 < d0:
        d0, d1 = d1, d0
    if (d1 - d0).days + 1 > max_days:
        raise ValueError(f"Range troppo ampio (> {max_days} giorni).")

    sc = StripeConnector()
    store = store or SupabaseStore()
    # Una sola pull balance-transactions per l'intero range, poi bucketing per giorno.
    txns = sc.balance_transactions(d0, d1)
    by_day = daily_from_balance_transactions(txns)
    rate = settlement_to_usd_rate(txns)
    days = 0
    cur = d0
    while cur <= d1:
        store.upsert_stripe_daily(cur.isoformat(), by_day.get(cur.isoformat(), {}))
        days += 1
        cur += timedelta(days=1)
    n_pay = store.upsert_stripe_payouts(
        convert_payouts_usd(sc.payouts(d0 - timedelta(days=7), d1 + timedelta(days=14)), rate))
    n_dis = store.upsert_stripe_disputes(sc.disputes(d0, d1 + timedelta(days=1)))
    return {"days": days, "payouts": n_pay, "disputes": n_dis,
            "range": f"{d0.isoformat()} → {d1.isoformat()}"}


def _persist(orders: list[dict], handle_map: dict[int, str], metrics: DailyMetrics,
             tw_summary: Optional[dict] = None) -> None:
    """Salva su Supabase se configurato; non blocca il report in caso di assenza DB."""
    try:
        from src.db.supabase_client import SupabaseStore

        store = SupabaseStore()
        store.upsert_orders(orders, handle_map)
        store.upsert_line_items(metrics)
        store.upsert_daily_metrics(metrics)
        _persist_product_units(store, metrics)
        _persist_sales_by_country(store, metrics.day, orders)
        _persist_sales_by_hour(store, metrics.day, orders)
        _persist_sales_by_source(store, metrics.day, orders)  # last-click (Fase 9)
        _persist_tw_pixel(store, metrics.day, tw_summary)      # pixel TW per canale (Fase 9)
        _persist_refunds(store, metrics.day, orders)         # refund Shopify (Fase 8)
        _persist_stripe(store, metrics.day)                  # Stripe daily + payout/dispute (Fase 8)
        print(
            f"[report] daily_metrics PERSISTED day={metrics.day} "
            f"orders={metrics.num_orders} revenue=${metrics.revenue:,.2f}"
        )
    except Exception as exc:  # il report deve arrivare comunque
        # ATTENZIONE: se questo fallisce, il giorno NON viene salvato -> buco in
        # daily_metrics (break-even/backfill ne risentono). Causa tipica: migration
        # mancante (es. colonna store_cvr) o Supabase non raggiungibile.
        print(f"[report] ⚠️ daily_metrics NON salvato per {metrics.day}: {exc}")


def backfill_daily_metrics(
    start_iso: str, end_iso: str, max_days: int = 60, shop=None, store=None
) -> list[tuple]:
    """
    Re-pull SHOPIFY e upsert daily_metrics per OGNI giorno in [start_iso, end_iso].
    Riempe/sovrascrive le righe (revenue, ordini, COGS). Solo Shopify: gli ad-spend
    NON vengono backfillati (net profit di quei giorni = solo Shopify).

    COGS RICALCOLATO dalla config CORRENTE: il backfill costruisce un resolver NUOVO
    leggendo config/cogs.yaml da disco in questo istante (NON usa il singleton
    @lru_cache di get_resolver(), che in un processo long-running — il bot — resta
    congelato sui valori caricati all'avvio). Così, dopo aver modificato cogs.yaml,
    /backfill riscrive davvero cogs_total con i nuovi costi. Azzeriamo anche la cache
    del singleton, così i report successivi nello stesso processo vedono i nuovi valori.

    VISITATORI reali: per ogni giorno tira anche le sessioni Shopify (ShopifyQL FROM
    sessions, scope read_reports). Se lo scope manca, store_sessions resta None e la
    dashboard stima i visitatori (ordini÷CVR).

    Ritorna una lista (giorno, ordini|'ERR', revenue, cogs_total, cogs_per_order, sessions)
    (o (giorno, 'ERR', messaggio) sugli errori) per il riepilogo.
    """
    from datetime import date as _date, timedelta as _td

    from src.config_loader import CogsResolver, get_resolver
    from src.db.supabase_client import SupabaseStore

    d0 = _date.fromisoformat(start_iso)
    d1 = _date.fromisoformat(end_iso)
    if d1 < d0:
        d0, d1 = d1, d0
    if (d1 - d0).days + 1 > max_days:
        raise ValueError(f"Range troppo ampio (> {max_days} giorni).")

    # Resolver FRESCO dal file su disco (immune al singleton stale). E invalida il
    # singleton, così build_daily_report/build_weekly_report successivi ricaricano.
    resolver = CogsResolver()
    get_resolver.cache_clear()

    shop = shop or ShopifyConnector()
    handle_map = shop.get_products_handle_map()  # uguale per tutti i giorni: una volta
    store = store or SupabaseStore()

    out: list[tuple] = []
    cur = d0
    while cur <= d1:
        w = day_window(cur)
        try:
            orders = _orders_in_window(shop.get_orders(w.start, w.end), w)  # boundary fix
            for o in orders:
                o["_day_rome"] = w.day_str
            m = compute_daily_metrics(
                w.day_str, orders, handle_map, resolver=resolver, ads_spend=0.0
            )
            # Visitatori reali (sessioni Shopify) del giorno: None se scope read_reports
            # mancante -> la colonna resta NULL e la dashboard stima (ordini÷CVR).
            m.store_sessions = _load_shopify_sessions(shop, w.day_str)
            store.upsert_orders(orders, handle_map)
            store.upsert_line_items(m)
            store.upsert_daily_metrics(m)
            _persist_product_units(store, m)   # unità vendute per prodotto (Fase 5)
            _persist_sales_by_country(store, w.day_str, orders)   # vendite per paese (Fase 6)
            _persist_sales_by_hour(store, w.day_str, orders)      # vendite per ora (Fase 7)
            _persist_sales_by_source(store, w.day_str, orders)    # last-click (Fase 9)
            _persist_refunds(store, w.day_str, orders)            # refund Shopify (Fase 8)
            cogs_per_order = (m.cogs_total / m.num_orders) if m.num_orders else 0.0
            out.append(
                (w.day_str, m.num_orders, round(m.revenue, 2),
                 round(m.cogs_total, 2), round(cogs_per_order, 2), m.store_sessions)
            )
        except Exception as exc:  # noqa: BLE001
            out.append((w.day_str, "ERR", str(exc)[:80]))
        cur += _td(days=1)
    return out


# Soglia di sanity per la Store CVR: un e-commerce reale non converte oltre il ~10%.
# Un valore superiore indica quasi certamente un errore di scala (×100) in un dato
# ancora salvato con la vecchia logica: meglio "n/a" che una percentuale assurda.
CVR_SANITY_MAX = 0.10  # 10%


def _fmt_cvr(cvr_fraction: Optional[float]) -> str:
    """
    CVR di negozio formattata in % (frazione -> percentuale). 'n/a' se assente/0.
    Guardia di sanity: se la frazione supera CVR_SANITY_MAX (10%) è quasi certamente
    un errore di scala (dato stale ×100) -> logga un warning e mostra 'n/a'.
    """
    cvr = float(cvr_fraction or 0)
    if cvr > CVR_SANITY_MAX:
        print(
            f"[report] ⚠️ Store CVR sospetta ({cvr * 100:.2f}% > "
            f"{CVR_SANITY_MAX * 100:.0f}%): probabile errore di scala -> mostro 'n/a'. "
            f"Ri-backfilla per correggere il valore salvato."
        )
        return "n/a"
    return f"{cvr * 100:.2f}%" if cvr > 0 else "n/a"


def _breakeven_line(breakeven) -> str:
    """
    Righe break-even (finestra 4 giorni, totali POOLED):
      - CONTRIBUTION break-even (esclude i costi fissi)
      - PROFIT break-even (include la quota fissa/ordine; dipende dal VOLUME ordini)
    'n/a' se non calcolabile.
    """
    be = breakeven or {}
    c_roas = f"{be['roas']:,.2f}x" if be.get("roas") else "n/a"
    c_cpa = f"${be['cpa']:,.2f}" if be.get("cpa") is not None else "n/a"
    p_roas = f"{be['profit_roas']:,.2f}x" if be.get("profit_roas") else "n/a"
    p_cpa = f"${be['profit_cpa']:,.2f}" if be.get("profit_cpa") is not None else "n/a"
    return (
        f"⚖️ Contribution break-even ROAS: {c_roas} · CPA: {c_cpa} (4-day pooled)\n"
        f"🎯 Profit break-even ROAS: {p_roas} · CPA: {p_cpa} "
        f"_(incl. fixed cost/order; depends on order volume)_"
    )


def _margin_line(m: DailyMetrics) -> str:
    """Margine % (deterministico): operating profit / revenue e net profit / revenue."""
    if m.revenue:
        op = m.net_profit_operativo / m.revenue * 100
        net = m.net_profit_netto / m.revenue * 100
        return f"📊 Margin — operating {op:.1f}% · net {net:.1f}%"
    return "📊 Margin — operating n/a · net n/a"


def _be_roas_x(be_roas: Optional[float]) -> str:
    """Break-even ROAS dinamico (media 4 giorni) per le sezioni piattaforma;
    se non disponibile usa il riferimento configurato (settings.BREAK_EVEN_ROAS)."""
    return f"{be_roas:,.2f}x" if be_roas else f"{settings.BREAK_EVEN_ROAS:.2f}x"


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
    header: Optional[str] = None,
) -> str:
    """
    Report Telegram in 3 sezioni (Markdown, tutto in USD):
      1) KEY METRICS  2) COST BREAKDOWN  3) PER-PLATFORM AD BREAKDOWN
    Tutti i numeri sono deterministici (nessun LLM). `header` opzionale per i report
    multi-giorno (es. 7 giorni); se assente, intestazione giornaliera standard.
    """
    out: list[str] = [header or f"📊 *Daily report — {m.day}* _(USD)_"]

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
        flow_rev = klaviyo_daily.get("flow_revenue")
        if flow_rev is not None:
            out.append(f"🔁 Klaviyo flow revenue: ${float(flow_rev):,.2f}")
    out.append(_margin_line(m))

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
    # ROAS di riferimento per gli ads = CONTRIBUTION break-even (il target per andare in pari).
    be_roas = (breakeven or {}).get("roas")
    section3 = (
        _format_meta_section(meta_daily, meta_campaigns, be_roas)
        + _format_tiktok_section(tiktok_daily, tiktok_campaigns, be_roas)
        + _format_google_section(google_daily, be_roas)
        + _format_klaviyo_section(klaviyo_daily, klaviyo_campaigns)
    )
    if section3.strip():
        out.append("\n*3) AD PLATFORMS*")
        out.append(section3.rstrip("\n"))

    return "\n".join(out) + "\n"


def _format_google_section(
    google_daily: Optional[dict], be_roas: Optional[float] = None
) -> str:
    """Sezione Google Ads: totali account (USD). Nessun breakdown per campagna (per ora)."""
    if not google_daily:
        if settings.TRIPLEWHALE_API_KEY:
            return "\n🔎 *Google Ads*: data not available for this day.\n"
        return ""

    spend = float(google_daily.get("spend") or 0)
    revenue = float(google_daily.get("revenue") or 0)
    roas = float(google_daily.get("roas") or 0)
    cpa = float(google_daily.get("cpa") or 0)
    # Conversioni FRAZIONARIE per il display: la CPA è corretta su conversioni frazionarie
    # (es. $41.73 ÷ 2.5 = $16.69), ma `orders` è salvato come intero (2). Ricaviamo il valore
    # frazionario da spend ÷ CPA così i numeri quadrano visivamente ("2.5").
    conv = (spend / cpa) if cpa > 0 else float(google_daily.get("orders") or 0)

    out = (
        f"\n🔎 *Google Ads — {google_daily.get('day')}* _(USD, via Triple Whale)_\n"
        f"   • Spend: *${spend:,.2f}*\n"
        f"   • ROAS: *{roas:,.2f}x* (break-even {_be_roas_x(be_roas)})\n"
        f"   • Attributed revenue: ${revenue:,.2f}\n"
    )
    if conv > 0:
        out += f"   • CPA: ${cpa:,.2f} · conversions: {conv:,.1f}\n"
    return out


def _format_tiktok_section(
    tiktok_daily: Optional[dict],
    tiktok_campaigns: Optional[list[dict]],
    be_roas: Optional[float] = None,
) -> str:
    """Sezione TikTok: totali + breakdown per campagna (USD). `be_roas` = break-even 4gg."""
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
        f"   • ROAS: *{roas:,.2f}x* (break-even {_be_roas_x(be_roas)})\n"
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
        f"   • ROAS: *{roas:,.2f}x* (break-even {_be_roas_x(be_roas)})\n"
        f"   • CPA: ${cpa:,.2f}\n"
        f"   • Attributed revenue: ${revenue:,.2f} · purchases: {orders}\n"
    )

    campaigns = meta_campaigns or []
    if campaigns:
        be_str = f" (break-even {_be_roas_x(be_roas)})"
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


# --------------------------------------------------------------------------- #
# Report 7 GIORNI — aggregato dalle righe già salvate (nessuna chiamata API).
# Stesso layout a 3 sezioni del /report giornaliero (riusa format_report).
# --------------------------------------------------------------------------- #
def _f(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _agg_ad_platform(rows: list[dict], day_set: set, label: str) -> Optional[dict]:
    """Totali 7gg di una piattaforma ads (Meta/TikTok/Google): spend/rev/orders + ROAS/CPA."""
    rows = [r for r in rows if r.get("day") in day_set]
    if not rows:
        return None
    spend = sum(_f(r.get("spend")) for r in rows)
    revenue = sum(_f(r.get("revenue")) for r in rows)
    orders = sum(int(r.get("orders") or 0) for r in rows)
    return {
        "day": label,
        "spend": spend,
        "revenue": revenue,
        "orders": orders,
        "roas": (revenue / spend) if spend else 0.0,   # da totali 7gg
        "cpa": (spend / orders) if orders else 0.0,     # da totali 7gg
    }


def _agg_klaviyo(rows: list[dict], day_set: set, label: str) -> Optional[dict]:
    rows = [r for r in rows if r.get("day") in day_set]
    if not rows:
        return None
    recipients = sum(int(r.get("recipients") or 0) for r in rows)
    opens = sum(int(r.get("opens") or 0) for r in rows)
    clicks = sum(int(r.get("clicks") or 0) for r in rows)
    flow_vals = [r.get("flow_revenue") for r in rows if r.get("flow_revenue") is not None]
    return {
        "day": label,
        "revenue": sum(_f(r.get("revenue")) for r in rows),
        "flow_revenue": (sum(_f(v) for v in flow_vals) if flow_vals else None),
        "opens": opens,
        "clicks": clicks,
        "conversions": sum(int(r.get("conversions") or 0) for r in rows),
        "recipients": recipients,
        "open_rate": (opens / recipients) if recipients else 0.0,
        "click_rate": (clicks / recipients) if recipients else 0.0,
    }


def _agg_campaigns(rows: list[dict], day_set: set, value_keys: tuple) -> list[dict]:
    """Aggrega le righe campagna per (id,nome) sommando i value_keys; calcola ROAS dai totali."""
    rows = [r for r in rows if r.get("day") in day_set]
    by: dict = {}
    for r in rows:
        key = (str(r.get("campaign_id") or ""), r.get("campaign_name") or "(no name)")
        acc = by.setdefault(key, {k: 0.0 for k in value_keys})
        for k in value_keys:
            acc[k] += _f(r.get(k))
    out: list[dict] = []
    for (cid, name), acc in by.items():
        row = {"campaign_id": cid, "campaign_name": name, **{k: acc[k] for k in value_keys}}
        if "spend" in acc:
            row["orders"] = int(acc.get("orders", 0))
            row["roas"] = (acc["revenue"] / acc["spend"]) if acc.get("spend") else 0.0
        out.append(row)
    out.sort(key=lambda c: c.get("spend", c.get("revenue", 0)), reverse=True)
    return out


def aggregate_week(
    daily_rows, meta_rows, tiktok_rows, google_rows, klaviyo_rows,
    meta_camp_rows, klaviyo_camp_rows, header=None,
):
    """Aggrega le righe DB di N giorni in (metrics, dicts piattaforma, breakeven, header).
    `header` opzionale: se assente, usa "{n}-day report — start → end"."""
    from src.metrics.profit import DailyMetrics, compute_breakeven_full

    rows = sorted(daily_rows, key=lambda r: r["day"])
    day_set = {r["day"] for r in rows}
    start, end = rows[0]["day"], rows[-1]["day"]
    label = f"{start} → {end}"

    m = DailyMetrics(day=label)

    def s(key):
        return sum(_f(r.get(key)) for r in rows)

    from src.metrics.fixed_costs import daily_fixed_allocation

    m.num_orders = int(s("num_orders"))
    m.revenue = s("revenue")
    m.cogs_total = s("cogs_total")
    m.shipping_total = s("shipping_total")
    m.payment_fees = s("payment_fees")
    # Quota costi fissi del periodo = Σ quota GIORNALIERA DATATA (valore in vigore a ogni
    # giorno), NON la somma dei valori stitati (che potrebbero essere stale se non
    # ri-backfillati). Così net profit e riga "Fixed allocation" del cost breakdown coincidono.
    m.fixed_cost_daily = sum(daily_fixed_allocation(r["day"]) for r in rows)
    # NB: ads_spend e net_profit NON si sommano dalle righe daily_metrics: quel campo è 0
    # per i giorni riscritti da /backfill (solo-Shopify), che però NON tocca le tabelle
    # ad (meta/google/tiktok). Ricalcoliamo il net profit dai componenti sommati + la
    # spesa ads REALE aggregata dalle tabelle piattaforma (vedi sotto, dopo l'aggregazione).
    m.aov = (m.revenue / m.num_orders) if m.num_orders else 0.0
    # Store CVR di PERIODO = conversioni totali / sessioni totali (metodo TOTALI, mai media
    # dei tassi giornalieri). PRIMARIO: sessioni REALI (colonna store_sessions, popolata dal
    # backfill) -> Σordini / Σstore_sessions. FALLBACK: ricostruzione da store_cvr per i
    # giorni che hanno il tasso ma non le sessioni. Bug precedente: si usava solo la
    # ricostruzione da store_cvr (che il backfill non imposta) -> CVR 0 -> "n/a" pur avendo
    # le sessioni reali salvate.
    real_orders = sum(_f(r.get("num_orders")) for r in rows if r.get("store_sessions") is not None)
    real_sessions = sum(
        _f(r.get("store_sessions")) for r in rows if r.get("store_sessions") is not None
    )
    if real_sessions > 0:
        m.store_cvr = real_orders / real_sessions
    else:
        tot_conv = 0.0
        tot_sessions = 0.0
        for r in rows:
            cvr_d = _f(r.get("store_cvr"))
            ord_d = _f(r.get("num_orders"))
            if cvr_d > 0 and ord_d > 0:
                tot_conv += ord_d
                tot_sessions += ord_d / cvr_d
        m.store_cvr = (tot_conv / tot_sessions) if tot_sessions > 0 else 0.0

    # break-even: resta a 4 giorni (i 4 più recenti del set), totali POOLED,
    # contribution + profit (dict).
    last4 = sorted(rows, key=lambda r: r["day"], reverse=True)[:4]
    breakeven = compute_breakeven_full(last4)

    meta_daily = _agg_ad_platform(meta_rows, day_set, label)
    tiktok_daily = _agg_ad_platform(tiktok_rows, day_set, label)
    google_daily = _agg_ad_platform(google_rows, day_set, label)
    klaviyo_daily = _agg_klaviyo(klaviyo_rows, day_set, label)
    meta_campaigns = _agg_campaigns(meta_camp_rows, day_set, ("spend", "revenue", "orders"))
    klaviyo_campaigns = _agg_campaigns(
        klaviyo_camp_rows, day_set, ("revenue", "opens", "clicks", "conversions")
    )

    # Spesa ads REALE del periodo dalle tabelle piattaforma (Meta + TikTok + Google),
    # robusta ai giorni backfillati (dove daily_metrics.ads_spend era 0). Net profit
    # ricalcolato dai componenti: così dashboard e /reportmonth combaciano ESATTAMENTE
    # e il margine mensile non è più gonfiato.
    m.ads_spend = (
        _f((meta_daily or {}).get("spend"))
        + _f((tiktok_daily or {}).get("spend"))
        + _f((google_daily or {}).get("spend"))
    )
    m.net_profit_operativo = (
        m.revenue - m.cogs_total - m.shipping_total - m.payment_fees - m.ads_spend
    )
    m.net_profit_netto = m.net_profit_operativo - m.fixed_cost_daily

    header = header or f"📊 *{len(rows)}-day report — {start} → {end}* _(USD)_"
    return (m, meta_daily, meta_campaigns, tiktok_daily, google_daily,
            klaviyo_daily, klaviyo_campaigns, breakeven, header)


def aggregate_period(daily_rows: list[dict], store, header=None):
    """
    Aggrega un periodo (righe daily_metrics già filtrate) nella STESSA struttura di
    /report7: tira le righe piattaforma per [min,max giorno] e chiama aggregate_week.

    Ritorna la tupla completa
      (m, meta_daily, meta_campaigns, tiktok_daily, google_daily,
       klaviyo_daily, klaviyo_campaigns, breakeven, header).

    È il punto di riuso condiviso dalla dashboard web: usando questa funzione i numeri
    della dashboard combaciano SEMPRE con i report Telegram (stessa aggregazione totals-based,
    stessa Store CVR di periodo, stesso break-even 4-giorni).
    """
    day_strs = sorted(r["day"] for r in daily_rows)
    start, end = day_strs[0], day_strs[-1]
    meta_rows = store.get_table_range("meta_daily", start, end)
    meta_camp_rows = store.get_table_range("meta_campaigns", start, end)
    tiktok_rows = store.get_table_range("tiktok_daily", start, end)
    google_rows = store.get_table_range("google_daily", start, end)
    klaviyo_rows = store.get_table_range("klaviyo_daily", start, end)
    klaviyo_camp_rows = store.get_table_range("klaviyo_campaigns", start, end)

    return aggregate_week(
        daily_rows, meta_rows, tiktok_rows, google_rows, klaviyo_rows,
        meta_camp_rows, klaviyo_camp_rows, header=header,
    )


def _sources_line(store, start: str, end: str) -> str:
    """
    Riga Telegram: prime sorgenti LAST-CLICK del periodo (ordini + revenue + %). Vuota se non
    ci sono dati. Include il caveat: il last-click sotto-conta gli ads vs le piattaforme.
    """
    try:
        from src.metrics.sales_source import top_sources

        rows = store.get_table_range("orders_by_source_daily", start, end)
        by: dict[str, dict] = {}
        for r in rows:
            s = r.get("source") or "other"
            acc = by.setdefault(s, {"orders": 0, "revenue": 0.0})
            acc["orders"] += int(r.get("orders") or 0)
            acc["revenue"] += float(r.get("revenue") or 0)
        tops = top_sources(by, 4)
        if not tops:
            return ""
        parts = [f"{t['source']} {t['orders']}·${t['revenue']:,.0f} ({t['pct']:.0f}%)"
                 for t in tops]
        return ("\n\n🧭 *Last-click sources* _(order landing; undercounts ads vs platform)_\n"
                + "  |  ".join(parts))
    except Exception as exc:  # noqa: BLE001 — riga accessoria, non deve rompere il report
        print(f"[report] ⚠️ sources line saltata: {exc}")
        return ""


def _render_multiday(daily_rows: list[dict], store, header=None) -> str:
    """Renderizza un report multi-giorno dalle righe daily_metrics fornite (gap-safe)."""
    (m, meta_daily, meta_campaigns, tiktok_daily, google_daily,
     klaviyo_daily, klaviyo_campaigns, breakeven, header) = aggregate_period(
        daily_rows, store, header=header,
    )
    # Klaviyo di PERIODO via query a finestra piena (campagne + flows). Se la query va a
    # buon fine, sostituisce i valori aggregati dal DB; altrimenti resta il fallback DB.
    day_strs = sorted(r["day"] for r in daily_rows)
    kp = load_klaviyo_period(day_strs[0], day_strs[-1])
    if kp.get("ok") and kp.get("daily"):
        klaviyo_daily, klaviyo_campaigns = kp["daily"], kp["campaigns"]
    text = format_report(
        m, meta_daily, meta_campaigns, klaviyo_daily, klaviyo_campaigns,
        tiktok_daily, [], google_daily, breakeven=breakeven, header=header,
    )
    return text + _sources_line(store, day_strs[0], day_strs[-1])


def build_weekly_report(days: int = 7, store=None) -> str:
    """Report a N giorni (default 7): N giorni più recenti CON DATI (gap-safe)."""
    if store is None:
        from src.db.supabase_client import SupabaseStore

        store = SupabaseStore()
    daily_rows = store.get_recent_daily_metrics(days=days)
    if not daily_rows:
        return f"📊 *{days}-day report* — no data available yet."
    return _render_multiday(daily_rows, store)


def build_month_report(store=None, today=None) -> str:
    """
    Report MESE CORRENTE (month-to-date): dal 1° del mese (Europe/Rome) al giorno
    più recente CON DATI. Stessa aggregazione totals-based dei report multi-giorno.
    """
    if store is None:
        from src.db.supabase_client import SupabaseStore

        store = SupabaseStore()
    if today is None:
        today = datetime.now(pytz.timezone(settings.TIMEZONE)).date()

    start_iso = today.replace(day=1).isoformat()
    end_iso = today.isoformat()
    daily_rows = store.get_daily_metrics_range(start_iso, end_iso)
    if not daily_rows:
        return f"📊 *Month-to-date report — {start_iso} → {end_iso}* — no data available yet."

    data_end = max(r["day"] for r in daily_rows)   # giorno più recente CON dati
    header = f"📊 *Month-to-date report — {start_iso} → {data_end}* _(USD)_"
    return _render_multiday(daily_rows, store, header=header)


def build_last_month_report(store=None, today=None) -> str:
    """
    Report MESE PRECEDENTE COMPLETO (mese solare intero prima di quello corrente):
    es. eseguito ad agosto -> 1–31 luglio. Stessa aggregazione totals-based dei report
    multi-giorno (ROAS/CPA/CVR dai totali di periodo; costi fissi = quota × giorni).

    L'header mostra i confini del mese SOLARE (start → ultimo giorno del mese), non
    l'ultimo giorno con dati: il mese è chiuso, quindi il range è quello del calendario.
    """
    from datetime import timedelta as _td

    if store is None:
        from src.db.supabase_client import SupabaseStore

        store = SupabaseStore()
    if today is None:
        today = datetime.now(pytz.timezone(settings.TIMEZONE)).date()

    first_this = today.replace(day=1)          # 1° del mese corrente
    last_prev = first_this - _td(days=1)        # ultimo giorno del mese scorso
    first_prev = last_prev.replace(day=1)       # 1° del mese scorso
    start_iso, end_iso = first_prev.isoformat(), last_prev.isoformat()

    daily_rows = store.get_daily_metrics_range(start_iso, end_iso)
    header = f"📊 *Last month report — {start_iso} → {end_iso}* _(USD)_"
    if not daily_rows:
        return f"📊 *Last month report — {start_iso} → {end_iso}* — no data available yet."
    return _render_multiday(daily_rows, store, header=header)


if __name__ == "__main__":
    # Stampa a video il report di ieri (Shopify reale), senza Telegram.
    _, _text = build_daily_report(persist=False)
    print(_text)
