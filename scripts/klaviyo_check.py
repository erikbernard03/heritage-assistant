#!/usr/bin/env python3
"""
Diagnostica LIVE Klaviyo (sola lettura). NON espone mai la API key.

Cosa fa:
- verifica che KLAVIYO_API_KEY sia presente (la mostra solo mascherata);
- elenca le metriche candidate e risolve la metrica di conversione ("Placed Order");
- esegue una pull per IERI e una per gli ULTIMI 7 GIORNI (Europe/Rome);
- stampa i dati per campagna (nome, revenue, opens, clicks, conversions) + i totali.

Esecuzione (dove la chiave è disponibile):
    # locale
    KLAVIYO_API_KEY=pk_xxx python scripts/klaviyo_check.py
    # Railway: One-off / Shell del servizio bot o cron
    python scripts/klaviyo_check.py
"""
from __future__ import annotations

import sys
from datetime import datetime, time, timedelta

import pytz

# permette `python scripts/klaviyo_check.py` dalla root del repo
sys.path.insert(0, ".")

from config import settings  # noqa: E402
from src.connectors.klaviyo import KlaviyoConnector  # noqa: E402
from src.metrics.klaviyo import compute_klaviyo_metrics  # noqa: E402


def _mask(v: str) -> str:
    return (v[:5] + "…" + v[-4:]) if v and len(v) > 12 else ("(set)" if v else "(EMPTY)")


def _rome_window(days_back_start: int, days_back_end: int):
    """Finestra [inizio, fine) in Europe/Rome. (1,1)=ieri intero; (7,1)=ultimi 7 giorni."""
    tz = pytz.timezone(settings.TIMEZONE)
    today = datetime.now(tz).date()
    start_d = today - timedelta(days=days_back_start)
    end_d = today - timedelta(days=days_back_end - 1)
    start = tz.localize(datetime.combine(start_d, time.min))
    end = tz.localize(datetime.combine(end_d, time.min))
    label = start_d.isoformat() if days_back_start == days_back_end else f"{start_d}…{(end_d - timedelta(days=1))}"
    return start.isoformat(), end.isoformat(), label


def _print_report(kc: KlaviyoConnector, metric_id: str, start_iso: str, end_iso: str, label: str):
    print(f"\n=== Window: {label}  ({start_iso} -> {end_iso}) ===")
    raw = kc.get_daily_campaign_report(start_iso, end_iso, metric_id)
    print(f"API returned {len(raw)} campaign rows.")
    if not raw:
        print("  (no campaign data for this window)")
        return
    ids = [str((r.get('groupings') or {}).get('campaign_id') or '') for r in raw]
    ids = [i for i in ids if i]
    names = kc.get_campaign_names(ids)
    computed = compute_klaviyo_metrics(label, raw, names=names)
    print(f"TOTAL  revenue=${computed.revenue:,.2f}  opens={computed.opens:,}  "
          f"clicks={computed.clicks:,}  conversions={computed.conversions:,}  "
          f"recipients={computed.recipients:,}")
    print("Per campaign (top by revenue):")
    for c in computed.campaigns[:15]:
        print(f"  - {c.campaign_name[:40]:40}  rev=${c.revenue:,.2f}  "
              f"opens={c.opens:,}  clicks={c.clicks:,}  conv={c.conversions}  id={c.campaign_id}")


def main() -> None:
    print("Klaviyo key:", _mask(settings.KLAVIYO_API_KEY))
    print("Klaviyo revision:", settings.KLAVIYO_API_REVISION)
    if not settings.KLAVIYO_API_KEY:
        print("\n❌ KLAVIYO_API_KEY non impostata in questo ambiente. Esporta la chiave e rilancia.")
        return

    kc = KlaviyoConnector()

    # 1) metriche candidate + risoluzione conversion metric
    print("\n--- Conversion metric resolution ---")
    try:
        data = kc._request("GET", "/metrics/")
        metrics = data.get("data", [])
        print(f"Metrics found: {len(metrics)}")
        for m in metrics:
            attrs = m.get("attributes") or {}
            name = attrs.get("name") or ""
            integ = attrs.get("integration")
            integ_name = integ.get("name") if isinstance(integ, dict) else integ
            if "order" in name.lower() or name in ("Placed Order", "Ordered Product"):
                print(f"  candidate: id={m.get('id')}  name={name!r}  integration={integ_name!r}")
    except Exception as exc:  # noqa: BLE001
        print(f"  metric listing failed: {exc}")

    try:
        metric_id = kc.resolve_conversion_metric_id()
        src = "env (KLAVIYO_CONVERSION_METRIC_ID)" if settings.KLAVIYO_CONVERSION_METRIC_ID else "auto-resolved"
        print(f"Resolved conversion_metric_id: {metric_id}  [{src}]")
    except Exception as exc:  # noqa: BLE001
        print(f"❌ Could not resolve conversion metric: {exc}")
        print("   -> set KLAVIYO_CONVERSION_METRIC_ID using one of the candidate ids above.")
        return

    # 2) pull ieri + ultimi 7 giorni
    for back_start, back_end in ((1, 1), (7, 1)):
        s, e, label = _rome_window(back_start, back_end)
        try:
            _print_report(kc, metric_id, s, e, label)
        except Exception as exc:  # noqa: BLE001
            print(f"  pull failed for {label}: {exc}")


if __name__ == "__main__":
    main()
