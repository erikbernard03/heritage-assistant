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


# keyword per scoprire i metricId di CPA/conversioni TikTok & Google nel Summary
_TILE_SCAN_KEYWORDS = ("cpa", "conversion", "purchase", "cost per", "tiktok", "google", "ga_")


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
