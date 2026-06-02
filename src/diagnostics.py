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

    from src.connectors.triplewhale import TripleWhaleConnector, extract_tiktok, find_tiktok_node
    from src.metrics.tiktok import compute_tiktok_metrics

    tw = TripleWhaleConnector()
    out.append(f"shopDomain used: {tw.shop_domain or '(EMPTY!)'}")

    # 0) validazione key + shops/permessi (GET /users/api-keys/me)
    out.append("\n— Key validation: GET /users/api-keys/me —")
    try:
        me = tw.get_me()
        snippet = json.dumps(me, ensure_ascii=False, default=str)
        out.append("✅ key valid. Response:")
        out.append(snippet[:1200] + ("…(truncated)" if len(snippet) > 1200 else ""))
    except Exception as exc:  # noqa: BLE001
        out.append(f"❌ /me call FAILED: {exc}")

    tz = pytz.timezone(settings.TIMEZONE)
    yesterday = (datetime.now(tz).date() - timedelta(days=1)).isoformat()

    out.append(f"\n— Summary {yesterday} (RAW STRUCTURE — mapping not finalized) —")
    try:
        summary = tw.get_summary(yesterday, yesterday)
    except Exception as exc:  # noqa: BLE001
        out.append(f"❌ summary call FAILED: {exc}")
        return _scrub("\n".join(out), settings.TRIPLEWHALE_API_KEY)

    out.append("✅ API call OK.")
    if isinstance(summary, dict):
        out.append("Top-level keys: " + ", ".join(list(summary.keys())[:20]))

    # raccoglie tutti i nodi-metrica (dict con 'metricId', oppure title+values)
    nodes: list[dict] = []

    def _collect(obj):
        if isinstance(obj, dict):
            if "metricId" in obj or ("title" in obj and "values" in obj):
                nodes.append(obj)
            for v in obj.values():
                _collect(v)
        elif isinstance(obj, list):
            for it in obj:
                _collect(it)

    _collect(summary)

    out.append(f"\nAll metric nodes ({len(nodes)}): title — metricId")
    for n in nodes[:80]:
        out.append(f"  • {n.get('title')!r} — metricId={n.get('metricId')!r}")

    # nodi che citano TikTok (per id/title/metricId); fallback al matcher generico
    def _is_tt(n: dict) -> bool:
        blob = f"{n.get('id')} {n.get('title')} {n.get('metricId')}".lower()
        return "tiktok" in blob

    tt_nodes = [n for n in nodes if _is_tt(n)]
    if not tt_nodes:
        one = find_tiktok_node(summary)
        if one is not None:
            tt_nodes = [one]

    out.append(f"\nTikTok-related nodes found: {len(tt_nodes)}")
    for idx, node in enumerate(tt_nodes[:6]):
        out.append(
            f"\n===== TikTok node #{idx} — title={node.get('title')!r} "
            f"metricId={node.get('metricId')!r} type={node.get('type')!r} ====="
        )
        out.append("keys: " + ", ".join(list(node.keys())))
        for field in ("values", "delta", "services"):
            blob = json.dumps(node.get(field), indent=2, ensure_ascii=False, default=str)
            cut = blob[:2600] + ("\n…(truncated)" if len(blob) > 2600 else "")
            out.append(f"\n--- {field} ---\n{cut}")
        charts = json.dumps(node.get("charts"), indent=2, ensure_ascii=False, default=str)
        cut = charts[:1800] + ("\n…(truncated)" if len(charts) > 1800 else "")
        out.append(f"\n--- charts (sample) ---\n{cut}")

    return _scrub("\n".join(out), settings.TRIPLEWHALE_API_KEY)
