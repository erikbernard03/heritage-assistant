#!/usr/bin/env python3
"""
Riconciliazione revenue Shopify ↔ DB per un intervallo (Europe/Rome).

Per ogni giorno stampa:
  - our_revenue      = Σ total_price degli ordini NON cancellati creati quel giorno Rome
                       (identica alla definizione di daily_metrics.revenue)
  - refunds_created  = Σ rimborsi su ordini CREATI quel giorno
  - refunds_processed= Σ rimborsi PROCESSATI quel giorno (anche su ordini di giorni precedenti)
  - orders           = n° ordini
  - boundary         = ordini creati entro ±120s da una mezzanotte (rischio boundary)
E, se passi le cifre Shopify, il diff our_revenue − shopify e quanto lo spiega refunds_processed.

Legge gli ordini dallo storico salvato su Supabase (tabella `orders`, colonna `raw`): nessun
ri-pull da Shopify. Fetch allargato indietro di 14g per catturare rimborsi tardivi su ordini
più vecchi.

Uso:
    python -m scripts.revenue_reconcile 2026-09-01 2026-09-04 \
        --shopify '{"2026-09-01":2810.80,"2026-09-02":2427.62,"2026-09-03":3376.73,"2026-09-04":3021.96}'
    python scripts/revenue_reconcile.py 2026-09-01 2026-09-04 --fixture orders.json
Nessun segreto in output.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta

import pytz

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import settings  # noqa: E402

TZ = pytz.timezone(settings.TIMEZONE)


def _f(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _rome_day(iso: str) -> str | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).astimezone(TZ).date().isoformat()
    except (ValueError, TypeError):
        return None


def _near_midnight(iso: str, secs: int = 120) -> bool:
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00")).astimezone(TZ)
    except (ValueError, TypeError):
        return False
    mins = dt.hour * 3600 + dt.minute * 60 + dt.second
    return mins <= secs or mins >= 86400 - secs


def _refund_amount(refund: dict) -> float:
    amt = 0.0
    for tx in (refund.get("transactions") or []):
        amt += _f(tx.get("amount"))
    if amt == 0.0:
        for adj in (refund.get("order_adjustments") or []):
            amt += abs(_f(adj.get("amount")))
    return amt


def _load(start: str, end: str, fixture: str | None) -> list[dict]:
    if fixture:
        with open(fixture, encoding="utf-8") as fh:
            return json.load(fh)
    from src.db.supabase_client import SupabaseStore

    store = SupabaseStore()
    wide_start = (datetime.fromisoformat(start).date() - timedelta(days=14)).isoformat()
    res = (store.client.table("orders").select("raw,day_rome")
           .gte("day_rome", wide_start).lte("day_rome", end).limit(20000).execute())
    return [r.get("raw") or {} for r in (res.data or [])]


def main() -> int:
    ap = argparse.ArgumentParser(description="Reconcile Shopify total_sales vs DB revenue.")
    ap.add_argument("start")
    ap.add_argument("end")
    ap.add_argument("--fixture")
    ap.add_argument("--shopify", help="JSON {day: total_sales} per il diff")
    args = ap.parse_args()

    shopify = json.loads(args.shopify) if args.shopify else {}
    orders = _load(args.start, args.end, args.fixture)
    print(f"{len(orders)} ordini caricati (finestra allargata -14g)\n")

    days = []
    d = datetime.fromisoformat(args.start).date()
    d1 = datetime.fromisoformat(args.end).date()
    while d <= d1:
        days.append(d.isoformat())
        d += timedelta(days=1)
    dayset = set(days)

    rev = defaultdict(float)
    cnt = defaultdict(int)
    ref_created = defaultdict(float)
    ref_processed = defaultdict(float)
    boundary = defaultdict(list)

    for o in orders:
        created_day = _rome_day(o.get("created_at"))
        cancelled = bool(o.get("cancelled_at"))
        if created_day in dayset and not cancelled:
            rev[created_day] += _f(o.get("total_price"))
            cnt[created_day] += 1
            if _near_midnight(o.get("created_at")):
                boundary[created_day].append(o.get("id") or o.get("order_number"))
        for rf in (o.get("refunds") or []):
            amt = _refund_amount(rf)
            if created_day in dayset:
                ref_created[created_day] += amt
            pday = _rome_day(rf.get("processed_at") or rf.get("created_at"))
            if pday in dayset:
                ref_processed[pday] += amt

    hdr = (f"{'day':<12}{'our_rev':>12}{'shopify':>12}{'diff':>10}"
           f"{'ref_proc':>10}{'ref_creat':>10}{'orders':>8}{'boundary':>9}")
    print(hdr)
    print("-" * len(hdr))
    tot_our = tot_shop = tot_diff = tot_refp = 0.0
    for day in days:
        our = rev[day]
        shop = _f(shopify.get(day)) if shopify else 0.0
        diff = (our - shop) if shopify else 0.0
        tot_our += our
        tot_shop += shop
        tot_diff += diff
        tot_refp += ref_processed[day]
        print(f"{day:<12}{our:>12,.2f}{(shop if shopify else 0):>12,.2f}{diff:>10,.2f}"
              f"{ref_processed[day]:>10,.2f}{ref_created[day]:>10,.2f}{cnt[day]:>8}"
              f"{len(boundary[day]):>9}")
    print("-" * len(hdr))
    print(f"{'TOTAL':<12}{tot_our:>12,.2f}{tot_shop:>12,.2f}{tot_diff:>10,.2f}"
          f"{tot_refp:>10,.2f}")

    if shopify:
        print(f"\nDiff (our − Shopify) over window: ${tot_diff:,.2f}")
        print(f"Refunds PROCESSED in window (Shopify nets these, we don't): ${tot_refp:,.2f}")
        residual = tot_diff - tot_refp
        print(f"Residual after refunds: ${residual:,.2f}  "
              f"(should be ~0 if refunds explain it; else check boundary orders above)")
        bdays = {d: boundary[d] for d in days if boundary[d]}
        if bdays:
            print(f"Boundary orders (±120s of midnight): {bdays}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
