"""
Diagnostica Klaviyo riutilizzabile (sola lettura) — usata sia dallo script CLI
(scripts/klaviyo_check.py) sia dal comando Telegram /klaviyo_check.

Ritorna SEMPRE testo (niente print): la API key non viene MAI inclusa nell'output
(solo mascherata). Esegue: risoluzione metrica di conversione + pull di ieri e
degli ultimi 7 giorni (SOLO campagne).
"""
from __future__ import annotations

import json
from datetime import datetime, time, timedelta

import pytz

from config import settings
from src.metrics.klaviyo import compute_klaviyo_metrics


def _mask(v: str) -> str:
    return (v[:5] + "…" + v[-4:]) if v and len(v) > 12 else ("(set)" if v else "(EMPTY)")


def _rome_window(days_back_start: int, days_back_end: int):
    """Finestra [inizio, fine) in Europe/Rome. (1,1)=ieri; (7,1)=ultimi 7 giorni."""
    tz = pytz.timezone(settings.TIMEZONE)
    today = datetime.now(tz).date()
    start_d = today - timedelta(days=days_back_start)
    end_d = today - timedelta(days=days_back_end - 1)
    start = tz.localize(datetime.combine(start_d, time.min))
    end = tz.localize(datetime.combine(end_d, time.min))
    label = (
        start_d.isoformat()
        if days_back_start == days_back_end
        else f"{start_d}…{end_d - timedelta(days=1)}"
    )
    return start.isoformat(), end.isoformat(), label


# keyword per scoprire i metricId di CPA/conversioni TikTok & Google nel Summary.
# 'pixel'/'facebook'/'conversionvalue' aggiunti per scoprire i metricId di attribuzione
# per canale (tw_pixel_daily): serve a distinguere pixel vero da platform-reported.
_TILE_SCAN_KEYWORDS = ("cpa", "conversion", "purchase", "cost per", "tiktok", "google",
                       "ga_", "pixel", "facebook", "conversionvalue")


def _scan_metric_tiles(summary: dict, keywords=_TILE_SCAN_KEYWORDS, limit: int = 150) -> list[str]:
    """
    Scansiona TUTTI i tile del Summary e ritorna 'title — metricId — values.current'
    per i tile il cui title o metricId contiene una delle keyword (case-insensitive).
    Serve a scoprire i metricId esatti (es. CPA/conversioni TikTok & Google).
    """
    tiles: list[tuple] = []

    def walk(obj):
        if isinstance(obj, dict):
            mid = obj.get("metricId")
            vals = obj.get("values")
            if mid is not None and isinstance(vals, dict) and "current" in vals:
                tiles.append((obj.get("title"), mid, vals["current"]))
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for it in obj:
                walk(it)

    walk(summary)
    kws = [k.lower() for k in keywords]
    lines: list[str] = []
    for title, mid, cur in tiles:
        blob = f"{title} {mid}".lower()
        if any(k in blob for k in kws):
            lines.append(f"  • {title!r} — {mid} — {cur}")
    if len(lines) > limit:
        lines = lines[:limit] + [f"  …(+{len(lines) - limit} more, refine keywords)"]
    return lines


def klaviyo_diagnostic() -> str:
    """Esegue la diagnostica Klaviyo e ritorna un report testuale (key mascherata)."""
    out: list[str] = []
    out.append("🔎 Klaviyo diagnostic (read-only, campaigns only)")
    out.append(f"Key: {_mask(settings.KLAVIYO_API_KEY)} · revision: {settings.KLAVIYO_API_REVISION}")

    if not settings.KLAVIYO_API_KEY:
        out.append("\n❌ KLAVIYO_API_KEY is not set in this environment.")
        return "\n".join(out)

    from src.connectors.klaviyo import KlaviyoConnector

    kc = KlaviyoConnector()

    # 1) metrica di conversione: candidati + risoluzione
    out.append("\n— Conversion metric —")
    try:
        data = kc._request("GET", "/metrics/")
        metrics = data.get("data", [])
        out.append(f"Metrics found: {len(metrics)}")
        shown = 0
        for m in metrics:
            attrs = m.get("attributes") or {}
            name = attrs.get("name") or ""
            if "order" in name.lower() or name in ("Placed Order", "Ordered Product"):
                integ = attrs.get("integration")
                integ_name = integ.get("name") if isinstance(integ, dict) else integ
                out.append(f"  • candidate id={m.get('id')} name={name!r} integ={integ_name!r}")
                shown += 1
                if shown >= 8:
                    break
    except Exception as exc:  # noqa: BLE001
        out.append(f"  metric listing failed: {exc}")

    try:
        metric_id = kc.resolve_conversion_metric_id()
        src = "from env" if settings.KLAVIYO_CONVERSION_METRIC_ID else "auto-resolved"
        out.append(f"✅ Resolved conversion_metric_id: {metric_id} [{src}]")
    except Exception as exc:  # noqa: BLE001
        out.append(f"❌ Could not resolve conversion metric: {exc}")
        out.append("→ Pick the 'Placed Order' id from the candidates above and set "
                    "KLAVIYO_CONVERSION_METRIC_ID on Railway.")
        return "\n".join(out)

    # 2) pull ieri + ultimi 7 giorni
    for back_start, back_end in ((1, 1), (7, 1)):
        s, e, label = _rome_window(back_start, back_end)
        out.append(f"\n— Window: {label} —")
        try:
            raw = kc.get_daily_campaign_report(s, e, metric_id)
            out.append(f"API returned {len(raw)} campaign rows.")
            if not raw:
                out.append("  (no campaign data for this window)")
                continue
            ids = [str((r.get("groupings") or {}).get("campaign_id") or "") for r in raw]
            ids = [i for i in ids if i]
            names = kc.get_campaign_names(ids)
            computed = compute_klaviyo_metrics(label, raw, names=names)
            out.append(
                f"TOTAL revenue=${computed.revenue:,.2f} opens={computed.opens:,} "
                f"clicks={computed.clicks:,} conv={computed.conversions:,}"
            )
            for c in computed.campaigns[:10]:
                out.append(
                    f"  • {c.campaign_name[:34]} — rev ${c.revenue:,.2f} · "
                    f"opens {c.opens:,} · conv {c.conversions}"
                )
        except Exception as exc:  # noqa: BLE001
            out.append(f"  pull failed: {exc}")

    return "\n".join(out)


def _scrub(text: str, secret: str) -> str:
    """Rimuove eventuali occorrenze del segreto dall'output (difesa in profondità)."""
    return text.replace(secret, "***") if secret else text


def meta_diagnostic() -> str:
    """
    Diagnostica LIVE Meta (sola lettura): currency + insights di ieri e degli ultimi
    7 giorni (totali per giorno + spesa per campagna). L'access token non è MAI esposto
    (mascherato e, per sicurezza, rimosso dall'output).
    """
    out: list[str] = []
    out.append("🔎 Meta diagnostic (read-only)")
    out.append(
        f"Token: {_mask(settings.META_ACCESS_TOKEN)} · account: "
        f"{settings.META_AD_ACCOUNT_ID or '(EMPTY)'} · api {settings.META_API_VERSION}"
    )

    if not (settings.META_ACCESS_TOKEN and settings.META_AD_ACCOUNT_ID):
        out.append("\n❌ META_ACCESS_TOKEN / META_AD_ACCOUNT_ID not set in this environment.")
        return "\n".join(out)

    from src.connectors.meta import MetaConnector
    from src.metrics.meta import fx_factor

    mc = MetaConnector()

    # 1) valuta account
    currency = "USD"
    out.append("\n— Ad account —")
    try:
        currency = mc.get_account_currency()
        out.append(f"✅ currency: {currency} (fx→USD: {fx_factor(currency):.4f})")
    except Exception as exc:  # noqa: BLE001
        out.append(f"❌ currency call FAILED: {exc}")

    # 1b) modalità di bucketing giorni (orario→Roma vs giornaliero fuso account)
    out.append("\n— Day bucketing —")
    try:
        from src.report import meta_bucketing_status

        st = meta_bucketing_status(mc)
        out.append(
            f"Mode: {st['mode']} · account_tz: {st.get('account_tz')} → "
            f"report_tz: {st.get('target_tz')}"
        )
        out.append(f"  {st.get('detail','')}")
        if st["mode"].startswith("hourly"):
            out.append(
                "  ℹ️ I numeri differiranno leggermente da Ads Manager (giorni fuso account)."
            )
    except Exception as exc:  # noqa: BLE001
        out.append(f"⚠️ bucketing status non determinato: {exc}")

    # 2) insights ultimi 7 giorni, per giorno (include 'ieri')
    tz = pytz.timezone(settings.TIMEZONE)
    today = datetime.now(tz).date()
    yesterday = today - timedelta(days=1)
    since = (today - timedelta(days=7)).isoformat()
    until = yesterday.isoformat()

    out.append(f"\n— Insights (per day) {since} → {until} —")
    try:
        rows = mc.get_campaign_insights(since, until, time_increment=1)
        out.append(f"✅ API OK: {len(rows)} campaign-day rows returned.")
        if not rows:
            out.append("  (no rows for this window — check date/timezone, account id, or spend)")

        # totali per giorno
        by_day: dict[str, dict] = {}
        for r in rows:
            d = r.get("date_start") or "?"
            agg = by_day.setdefault(d, {"spend": 0.0, "impr": 0, "clicks": 0, "camps": set()})
            agg["spend"] += float(r.get("spend") or 0)
            agg["impr"] += int(float(r.get("impressions") or 0))
            agg["clicks"] += int(float(r.get("clicks") or 0))
            if r.get("campaign_id"):
                agg["camps"].add(r["campaign_id"])
        for d in sorted(by_day):
            a = by_day[d]
            flag = " ← yesterday" if d == until else ""
            out.append(
                f"  {d}{flag}: spend {a['spend']:,.2f} {currency} · "
                f"impr {a['impr']:,} · clicks {a['clicks']:,} · campaigns {len(a['camps'])}"
            )

        # spesa per campagna (totale finestra)
        by_camp: dict[tuple, float] = {}
        for r in rows:
            key = (r.get("campaign_id") or "?", r.get("campaign_name") or "(no name)")
            by_camp[key] = by_camp.get(key, 0.0) + float(r.get("spend") or 0)
        if by_camp:
            out.append("\nPer-campaign spend (window total, top 10):")
            for (cid, name), sp in sorted(by_camp.items(), key=lambda kv: kv[1], reverse=True)[:10]:
                out.append(f"  • {name[:34]} — {sp:,.2f} {currency}  id={cid}")
    except Exception as exc:  # noqa: BLE001
        out.append(f"❌ insights call FAILED: {exc}")

    return _scrub("\n".join(out), settings.META_ACCESS_TOKEN)


def triplewhale_diagnostic() -> str:
    """
    Diagnostica LIVE Triple Whale (sola lettura, SOLO TikTok): chiama il Summary per
    ieri e per gli ultimi 7 giorni, mostra esito/errore, struttura della risposta,
    e le metriche TikTok estratte (spend/ROAS/impr/clicks/conv + campagne).
    La API key non è MAI esposta (mascherata + rimossa dall'output).
    """
    out: list[str] = []
    out.append("🔎 Triple Whale diagnostic (read-only, TikTok only)")
    out.append(
        f"Key: {_mask(settings.TRIPLEWHALE_API_KEY)} · base: {settings.TRIPLEWHALE_API_BASE} · "
        f"path: {settings.TRIPLEWHALE_SUMMARY_PATH}"
    )

    if not settings.TRIPLEWHALE_API_KEY:
        out.append("\n❌ TRIPLEWHALE_API_KEY not set in this environment.")
        return "\n".join(out)

    from src.connectors.triplewhale import TripleWhaleConnector, extract_tiktok
    from src.metrics.tiktok import compute_tiktok_metrics

    tw = TripleWhaleConnector()
    out.append(f"shopDomain used: {tw.shop_domain or '(EMPTY!)'}")

    # 0) validazione key + shops/permessi (GET /users/api-keys/me)
    out.append("\n— Key validation: GET /users/api-keys/me —")
    try:
        me = tw.get_me()
        snippet = json.dumps(me, ensure_ascii=False, default=str)
        out.append("✅ key valid. Response:")
        out.append(snippet[:900] + ("…(truncated)" if len(snippet) > 900 else ""))
    except Exception as exc:  # noqa: BLE001
        out.append(f"❌ /me call FAILED: {exc}")

    # 1) metriche TikTok calcolate per ieri + ultimi 7 giorni
    tz = pytz.timezone(settings.TIMEZONE)
    today = datetime.now(tz).date()
    yesterday = (today - timedelta(days=1)).isoformat()
    since7 = (today - timedelta(days=7)).isoformat()

    for label, start, end in (
        ("yesterday", yesterday, yesterday),
        ("last 7 days", since7, (today - timedelta(days=1)).isoformat()),
    ):
        out.append(f"\n— TikTok {label} ({start} → {end}) —")
        try:
            summary = tw.get_summary(start, end)
            if label == "yesterday":
                out.append("🔍 Metric discovery (title — metricId — values.current):")
                out.extend(_scan_metric_tiles(summary))
            tk = extract_tiktok(summary)
            if not tk:
                out.append("⚠️ No TikTok metrics found via current mapping.")
                continue
            c = compute_tiktok_metrics(label, tk)
            out.append(
                f"✅ Spend: ${c.spend:,.2f} "
                f"(tracked ${tk['tracked_spend']:,.2f} + GMV-Max ${tk['non_tracked_spend']:,.2f})"
            )
            out.append(f"ROAS: {c.roas:,.2f}x · Revenue (tiktokConversionValue): ${c.revenue:,.2f}")
            out.append(
                f"Impressions: {c.impressions:,} · Clicks: {c.clicks:,} · CPM: ${tk['cpm']:,.2f}"
            )
            if c.orders:
                out.append(f"Conversions: {c.orders} · CPA: ${c.cpa:,.2f}")
            else:
                out.append("Conversions: n/a (no TikTok purchases metric → CPA skipped)")
        except Exception as exc:  # noqa: BLE001
            out.append(f"❌ call FAILED: {exc}")

    return _scrub("\n".join(out), settings.TRIPLEWHALE_API_KEY)


def google_diagnostic() -> str:
    """
    Diagnostica LIVE Google Ads (sola lettura, via Triple Whale Summary): metriche
    Google calcolate per ieri e gli ultimi 7 giorni (spend/ROAS/CPA/clicks/impr/conv).
    La API key non è MAI esposta (mascherata + rimossa dall'output).
    """
    out: list[str] = []
    out.append("🔎 Google Ads diagnostic (read-only, via Triple Whale)")
    out.append(
        f"Key: {_mask(settings.TRIPLEWHALE_API_KEY)} · base: {settings.TRIPLEWHALE_API_BASE} · "
        f"path: {settings.TRIPLEWHALE_SUMMARY_PATH}"
    )
    if not settings.TRIPLEWHALE_API_KEY:
        out.append("\n❌ TRIPLEWHALE_API_KEY not set in this environment.")
        return "\n".join(out)

    from src.connectors.triplewhale import TripleWhaleConnector, extract_google
    from src.metrics.google import compute_google_metrics

    tw = TripleWhaleConnector()
    out.append(f"shopDomain used: {tw.shop_domain or '(EMPTY!)'}")

    tz = pytz.timezone(settings.TIMEZONE)
    today = datetime.now(tz).date()
    yesterday = (today - timedelta(days=1)).isoformat()
    since7 = (today - timedelta(days=7)).isoformat()

    for label, start, end in (
        ("yesterday", yesterday, yesterday),
        ("last 7 days", since7, (today - timedelta(days=1)).isoformat()),
    ):
        out.append(f"\n— Google {label} ({start} → {end}) —")
        try:
            summary = tw.get_summary(start, end)
            if label == "yesterday":
                out.append("🔍 Metric discovery (title — metricId — values.current):")
                out.extend(_scan_metric_tiles(summary))
            g = extract_google(summary)
            if not g:
                out.append("⚠️ No Google metrics found via current mapping.")
                continue
            from src.connectors.triplewhale import extract_store_cvr, store_cvr_debug

            cvr = extract_store_cvr(summary)
            c = compute_google_metrics(label, g, store_cvr=cvr or 0.0)
            out.append(f"✅ Spend: ${c.spend:,.2f} · ROAS: {c.roas:,.2f}x")
            out.append(f"Revenue: ${c.revenue:,.2f} · conversions: {c.orders} · CPA: ${c.cpa:,.2f}")
            out.append(f"Impressions: {c.impressions:,} · Clicks: {c.clicks:,}")
            cvr_str = f"{c.store_cvr * 100:.2f}%" if c.store_cvr > 0 else "n/a"
            dbg = store_cvr_debug(summary)
            method = (
                "pixelPurchases/sessions"
                if (dbg.get("pixelPurchases") and dbg.get("sessions"))
                else "pixelConversionRate"
            )
            out.append(f"Store CVR: {cvr_str}  [source: {method}]")
            out.append(
                f"   raw: pixelConversionRate={dbg.get('pixelConversionRate')} · "
                f"pixelPurchases={dbg.get('pixelPurchases')} · "
                f"sessions={dbg.get('sessions')} ({dbg.get('sessions_metricId')})"
            )
        except Exception as exc:  # noqa: BLE001
            out.append(f"❌ call FAILED: {exc}")

    # Attribuzione per canale risolta (tw_pixel_daily): mostra QUALI metricId sono stati scelti
    # e se sono 'pixel' o 'platform-reported'. Serve a confermare il wiring dopo la discovery.
    out.append("\n— Channel attribution resolved (feeds tw_pixel_daily) —")
    try:
        from src.connectors.triplewhale import extract_pixel_attribution

        summ = tw.get_summary(yesterday, yesterday)
        px = extract_pixel_attribution(summ)
        if not px:
            out.append("⚠️ No attribution metrics matched (per-channel columns will be empty).")
        for ch, v in px.items():
            out.append(
                f"  {ch}: orders={float(v.get('orders') or 0):,.1f} "
                f"rev=${float(v.get('revenue') or 0):,.2f} · kind={v.get('kind')} "
                f"[{v.get('orders_metric')} / {v.get('revenue_metric')}]")
    except Exception as exc:  # noqa: BLE001
        out.append(f"❌ attribution probe failed: {exc}")

    # CVR primaria: Shopify (ShopifyQL FROM sessions) — verifica scope read_reports
    out.append("\n— Shopify Store CVR (primary, ShopifyQL FROM sessions) —")
    try:
        from src.connectors.shopify import ShopifyConnector

        sc = ShopifyConnector().get_session_conversion_rate(yesterday)
        if sc is None:
            out.append(
                "⚠️ Shopify CVR not available — likely missing 'read_reports' scope "
                "(or analytics access). Falling back to Triple Whale pixelConversionRate."
            )
        else:
            out.append(f"✅ Shopify CVR (yesterday): {sc * 100:.2f}%  → used as Store CVR.")
    except Exception as exc:  # noqa: BLE001
        out.append(f"❌ Shopify CVR call failed: {exc}")

    return _scrub("\n".join(out), settings.TRIPLEWHALE_API_KEY)


# Scope Admin API attesi dall'app (vedi shopify.app.toml).
_SHOPIFY_NEEDED_SCOPES = [
    "read_orders", "read_all_orders", "read_products",
    "read_fulfillments", "read_inventory", "read_reports",
]


def stripe_diagnostic() -> str:
    """
    Diagnostica LIVE Stripe (sola lettura): ieri (gross/fee/net/refund) + fee rate vs 7.5%,
    payout recenti e dispute aperte con scadenza evidenze. La API key non è MAI esposta.
    """
    out: list[str] = ["💳 Stripe diagnostic (read-only)"]
    out.append(f"Key: {_mask(settings.STRIPE_API_KEY)}")
    if not settings.STRIPE_API_KEY:
        out.append("\n❌ STRIPE_API_KEY not set in this environment.")
        return "\n".join(out)

    from datetime import timedelta

    from src.connectors.stripe_conn import StripeConnector
    from src.metrics.stripe_metrics import (
        convert_payouts_usd,
        daily_from_balance_transactions,
        dispute_rate,
        settlement_to_usd_rate,
        total_payment_cost_rate,
    )

    try:
        sc = StripeConnector()
    except Exception as exc:  # noqa: BLE001
        out.append(f"\n❌ connector init failed: {exc}")
        return "\n".join(out)

    tz = pytz.timezone(settings.TIMEZONE)
    today = datetime.now(tz).date()
    yday = today - timedelta(days=1)

    # Finestra 35g per il tasso di cambio effettivo (settlement → USD) e la valuta di settlement.
    settle_rate = 1.0
    settle_cur = "usd"
    try:
        win = sc.balance_transactions(today - timedelta(days=35), today)
        settle_rate = settlement_to_usd_rate(win)
        curs = {str(t.get("currency") or "usd").lower() for t in win
                if str(t.get("type") or "").lower() in ("charge", "payment")}
        if curs:
            settle_cur = sorted(curs)[0]
        out.append(f"Settlement currency: {settle_cur.upper()} · "
                   f"effective rate → USD {settle_rate:.5f}"
                   + ("  (USD account — no conversion)" if settle_cur == "usd" else ""))
    except Exception:  # noqa: BLE001
        pass

    out.append(f"\n— Yesterday {yday.isoformat()} —")
    try:
        agg = daily_from_balance_transactions(sc.balance_transactions(yday, yday)).get(
            yday.isoformat(), {})
        gross = float(agg.get("gross") or 0)
        fee = float(agg.get("fee") or 0)
        net = float(agg.get("net") or 0)
        rates = total_payment_cost_rate(gross, fee)
        out.append(f"  Gross ${gross:,.2f} · Fee ${fee:,.2f} · Net ${net:,.2f}")
        out.append(f"  Charges {int(agg.get('charge_count') or 0)} · "
                   f"Refunds {int(agg.get('refund_count') or 0)} (${float(agg.get('refund_amount') or 0):,.2f})")
        est = settings.FEE_PAGAMENTI
        if rates["total_rate"] is not None:
            sr, su, tot = rates["stripe_rate"], rates["surcharge_rate"], rates["total_rate"]
            # Il confronto è sul TOTALE (fee Stripe + surcharge Shopify), non sulla sola fee.
            flag = "≈ matches" if abs(tot - est) < 0.01 else "⚠️ differs from"
            out.append(
                f"  Stripe fee {sr*100:.2f}% + Shopify surcharge {su*100:.2f}% "
                f"= est. total {tot*100:.2f}% — {flag} the {est*100:.1f}% assumption")
            if su == 0:
                out.append("  ⚠️ SHOPIFY_GATEWAY_SURCHARGE_PCT not set — Shopify's gateway "
                           "surcharge is invisible to Stripe; the fee above understates your true cost.")
    except Exception as exc:  # noqa: BLE001
        out.append(f"  ❌ balance-transactions call failed: {exc}")

    out.append("\n— Recent payouts (last 35 days) —")
    try:
        pays = convert_payouts_usd(
            sc.payouts(today - timedelta(days=35), today + timedelta(days=7)), settle_rate)
        for p in sorted(pays, key=lambda x: x.get("arrival_date") or "", reverse=True)[:5]:
            out.append(f"  {p.get('arrival_date')}: ${float(p.get('amount') or 0):,.2f} "
                       f"· {p.get('status')}")
        if not pays:
            out.append("  (none)")
    except Exception as exc:  # noqa: BLE001
        out.append(f"  ❌ payouts call failed: {exc}")

    out.append("\n— Disputes (last 120 days) —")
    try:
        disp = sc.disputes(today - timedelta(days=120), today)
        charges_90 = daily_from_balance_transactions(
            sc.balance_transactions(today - timedelta(days=90), today))
        n_charges = sum(int(v.get("charge_count") or 0) for v in charges_90.values())
        dr = dispute_rate(len(disp), n_charges)
        open_d = [d for d in disp if str(d.get("status")) in
                  ("needs_response", "warning_needs_response", "under_review")]
        for d in open_d[:6]:
            out.append(f"  {d.get('id')}: ${float(d.get('amount') or 0):,.2f} · {d.get('status')} "
                       f"· {d.get('reason')} · evidence due {d.get('evidence_due') or 'n/a'}")
        out.append(f"  Open: {len(open_d)} / total {len(disp)}")
        if dr is not None:
            flag = " ⚠️ approaching 1%!" if dr >= 0.008 else ""
            out.append(f"  Dispute rate (90d): {dr*100:.3f}% ({len(disp)}/{n_charges}){flag}")
    except Exception as exc:  # noqa: BLE001
        out.append(f"  ❌ disputes call failed: {exc}")

    return _scrub("\n".join(out), settings.STRIPE_API_KEY)


def shopify_diagnostic() -> str:
    """
    Diagnostica LIVE Shopify (sola lettura): mostra gli scope EFFETTIVAMENTE concessi al
    token corrente e fa due prove funzionali — ordini oltre i 60 giorni (read_all_orders)
    e sessioni/visitatori (read_reports). Nessun segreto in output (client_id mascherato,
    token mai stampato).
    """
    out: list[str] = ["🔎 Shopify diagnostic (read-only)"]
    out.append(
        f"Store: {settings.SHOPIFY_STORE or '(EMPTY)'} · "
        f"client_id: {_mask(settings.SHOPIFY_CLIENT_ID)} · api {settings.SHOPIFY_API_VERSION}"
    )
    if not (settings.SHOPIFY_STORE and settings.SHOPIFY_CLIENT_ID
            and settings.SHOPIFY_CLIENT_SECRET):
        out.append("\n❌ Shopify credentials not set in this environment.")
        return "\n".join(out)

    from src.connectors.shopify import ShopifyConnector

    shop = ShopifyConnector()

    # 1) scope concessi (dal grant client_credentials, campo `scope`)
    out.append("\n— Granted Admin API scopes (live token) —")
    try:
        granted = set(shop.get_granted_scopes())
    except Exception as exc:  # noqa: BLE001
        out.append(f"❌ could not obtain token / scopes: {exc}")
        return "\n".join(out)
    for s in _SHOPIFY_NEEDED_SCOPES:
        out.append(f"  {'✅' if s in granted else '❌'} {s}")
    extra = sorted(granted - set(_SHOPIFY_NEEDED_SCOPES))
    if extra:
        out.append(f"  (other granted: {', '.join(extra)})")

    tz = pytz.timezone(settings.TIMEZONE)
    today = datetime.now(tz).date()

    def _count_orders(d):
        start = tz.localize(datetime.combine(d, time.min))
        return len(shop.get_orders(start, start + timedelta(days=1)))

    # 2) prova ordini: ieri (recente) + ~75 giorni fa (oltre i 60gg = read_all_orders)
    out.append("\n— Orders probe —")
    try:
        y = today - timedelta(days=1)
        out.append(f"  {y} (recent): {_count_orders(y)} orders")
    except Exception as exc:  # noqa: BLE001
        out.append(f"  ❌ recent orders call failed: {exc}")
    try:
        old = today - timedelta(days=75)
        n_old = _count_orders(old)
        if n_old > 0:
            flag = "✅ read_all_orders is working (returns >60-day orders)"
        else:
            flag = "⚠️ 0 — no orders that day OR read_all_orders still missing"
        out.append(f"  {old} (>60 days): {n_old} orders — {flag}")
    except Exception as exc:  # noqa: BLE001
        out.append(f"  ❌ >60-day orders call failed: {exc}")

    # 3) prova sessioni/visitatori (read_reports, ShopifyQL FROM sessions).
    #    NON assumiamo "scope mancante": stampiamo l'errore REALE della query se c'è.
    out.append("\n— Sessions / visitors probe (ShopifyQL FROM sessions) —")
    try:
        y = today - timedelta(days=1)
        sess, err, _raw = shop.get_sessions_debug(y.isoformat())
        if sess is not None:
            out.append(f"  ✅ real Shopify sessions for {y}: {sess:,}")
        elif err:
            out.append(f"  ❌ ShopifyQL query FAILED (not a scope issue): {err}")
        else:
            out.append(f"  ⚠️ no rows returned for {y} (no data that day?).")
    except Exception as exc:  # noqa: BLE001
        out.append(f"  ❌ sessions call raised: {exc}")

    # 4) verdetto sui due scope target
    missing = [s for s in ("read_all_orders", "read_reports") if s not in granted]
    if missing:
        out.append(
            f"\n➡️ Missing: {', '.join(missing)}. Release a new app version with these "
            "scopes and re-approve the app on the store, then /backfill."
        )
    else:
        out.append(
            "\n✅ read_all_orders + read_reports granted — /backfill can restore old "
            "orders and fill real visitors."
        )
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# Audit di un singolo giorno: confronto report-revenue vs subtotal/tax/shipping,
# refund e ordini al confine di mezzanotte (Europe/Rome).
# --------------------------------------------------------------------------- #
def _f(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def summarize_orders(orders: list[dict]) -> dict:
    """Aggregazione pura degli ordini di un giorno (testabile, niente rete)."""
    from src.metrics.profit import _order_shipping_collected, _order_tax_collected

    active = [o for o in orders if not o.get("cancelled_at")]
    cancelled = [o for o in orders if o.get("cancelled_at")]

    def _current(o):
        c = o.get("current_total_price")
        return _f(c) if c is not None else _f(o.get("total_price"))

    total = sum(_f(o.get("total_price")) for o in active)
    current = sum(_current(o) for o in active)
    refunded_orders = [
        o for o in active
        if (_f(o.get("total_price")) - _current(o) > 0.005)
        or o.get("financial_status") in ("refunded", "partially_refunded")
    ]
    return {
        "n_active": len(active),
        "n_cancelled": len(cancelled),
        "total_price": total,                                        # = report revenue
        "current_total": current,                                   # dopo i refund
        "refunded_amount": total - current,
        "subtotal": sum(_f(o.get("subtotal_price")) for o in active),  # prodotto (post-sconti, no tax/ship)
        "tax": sum(_order_tax_collected(o) for o in active),
        "shipping": sum(_order_shipping_collected(o) for o in active),
        "cancelled_total": sum(_f(o.get("total_price")) for o in cancelled),
        "refunded_orders": refunded_orders,
    }


def day_audit(day: str) -> str:
    """Audit completo di un giorno (Europe/Rome): report revenue vs componenti."""
    from datetime import date as _date

    from src.connectors.shopify import ShopifyConnector
    from src.report import day_window

    out = [f"🔍 Day audit — {day} (Europe/Rome)"]
    try:
        w = day_window(_date.fromisoformat(day))
    except Exception as exc:  # noqa: BLE001
        return f"❌ Bad date: {exc}"

    try:
        orders = ShopifyConnector().get_orders(w.start, w.end)
    except Exception as exc:  # noqa: BLE001
        return "\n".join(out + [f"❌ Shopify pull failed: {exc}"])

    s = summarize_orders(orders)
    out.append(f"Active orders: {s['n_active']} · cancelled: {s['n_cancelled']}")
    out.append(f"Σ total_price (REPORT revenue): ${s['total_price']:,.2f}")
    out.append(
        f"Σ current_total_price (after refunds): ${s['current_total']:,.2f}  "
        f"→ refunded ${s['refunded_amount']:,.2f}"
    )
    out.append(f"Σ subtotal_price (product only, post-discount, excl tax+ship): ${s['subtotal']:,.2f}")
    out.append(f"Σ total_tax (VAT collected): ${s['tax']:,.2f}")
    out.append(f"Σ shipping collected: ${s['shipping']:,.2f}")
    out.append(f"check subtotal+tax+shipping = ${s['subtotal'] + s['tax'] + s['shipping']:,.2f}")
    out.append(
        f"so: revenue−tax = ${s['total_price'] - s['tax']:,.2f} · "
        f"revenue−tax−shipping = ${s['total_price'] - s['tax'] - s['shipping']:,.2f}"
    )

    # stored daily_metrics
    try:
        from src.db.supabase_client import SupabaseStore

        row = SupabaseStore().get_daily_metrics_for_day(day)
        if row:
            out.append(
                f"stored daily_metrics: revenue ${_f(row.get('revenue')):,.2f} · "
                f"orders {row.get('num_orders')}"
            )
        else:
            out.append("stored daily_metrics: (no row for this day)")
    except Exception as exc:  # noqa: BLE001
        out.append(f"daily_metrics read failed: {exc}")

    # ordini al confine di mezzanotte (±30 min dai due bordi)
    tz = pytz.timezone(settings.TIMEZONE)
    out.append("Orders within ±30 min of a midnight boundary:")
    found = 0
    for o in orders:
        if o.get("cancelled_at"):
            continue
        ca = o.get("created_at")
        if not ca:
            continue
        try:
            dt = datetime.fromisoformat(ca.replace("Z", "+00:00")).astimezone(tz)
        except Exception:  # noqa: BLE001
            continue
        near_start = abs((dt - w.start).total_seconds()) < 1800
        near_end = abs((w.end - dt).total_seconds()) < 1800
        if near_start or near_end:
            out.append(
                f"  • {dt.strftime('%Y-%m-%d %H:%M:%S')} Rome · ${_f(o.get('total_price')):,.2f} "
                f"· #{o.get('order_number') or o.get('name')}"
            )
            found += 1
    if not found:
        out.append("  (none)")

    # refund
    out.append(f"Refunded / partially-refunded active orders: {len(s['refunded_orders'])}")
    for o in s["refunded_orders"][:10]:
        out.append(
            f"  • #{o.get('order_number') or o.get('name')} status={o.get('financial_status')} "
            f"total=${_f(o.get('total_price')):,.2f} current=${_f(o.get('current_total_price')):,.2f}"
        )
    return "\n".join(out)
