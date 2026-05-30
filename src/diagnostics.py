"""
Diagnostica Klaviyo riutilizzabile (sola lettura) — usata sia dallo script CLI
(scripts/klaviyo_check.py) sia dal comando Telegram /klaviyo_check.

Ritorna SEMPRE testo (niente print): la API key non viene MAI inclusa nell'output
(solo mascherata). Esegue: risoluzione metrica di conversione + pull di ieri e
degli ultimi 7 giorni (SOLO campagne).
"""
from __future__ import annotations

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
