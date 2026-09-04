#!/usr/bin/env python3
"""
Audit della classificazione LAST-CLICK per sorgente su un intervallo di giorni.

- Stampa un CAMPIONE degli ordini il cui landing_site/referring_site/UTM contiene
  'klaviyo', 'email', '_kx' o 'klclid' (segnali Klaviyo), con la stringa grezza e la
  classificazione VECCHIA → NUOVA.
- Conta quanti ordini si RICLASSIFICANO come "email" grazie al segnale _kx/klclid.
- Conta i referrer INTERNI (heritagering.com / myshopify) che prima trapelavano come
  sorgente e ora diventano "direct".

Legge gli ordini dallo storico salvato in Supabase (tabella `orders`, colonna `raw` = ordine
Shopify completo), quindi NON serve ripullare Shopify.

Uso:
    python -m scripts.source_audit 2026-08-01 2026-08-31
    python scripts/source_audit.py 2026-08-01 2026-08-31 --fixture sample.json
Nessun segreto in output.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.metrics.sales_source import (  # noqa: E402
    _EMAIL_MEDIUMS,
    _EMAIL_SOURCES,
    _META_REF,
    _META_SOURCES,
    _PAID_MEDIUMS,
    _PINTEREST_SOURCES,
    _TIKTOK_SOURCES,
    _referrer_domain,
    _utm_from_order,
    classify_order,
)


def _old_classify(order: dict) -> str:
    """Classificatore PRIMA del fix (nessun _kx, nessun blanking dei referrer interni)."""
    src, med = _utm_from_order(order)
    ref = _referrer_domain(order)
    if src in _META_SOURCES or any(d in ref for d in _META_REF):
        return "meta"
    if src in _TIKTOK_SOURCES or "tiktok.com" in ref:
        return "tiktok"
    if src in _PINTEREST_SOURCES or "pinterest." in ref:
        return "pinterest"
    if src in _EMAIL_SOURCES or med in _EMAIL_MEDIUMS:
        return "email"
    is_google_src = (src == "google") or ("google." in ref) or (src == "googleads")
    is_paid = med in _PAID_MEDIUMS
    if src in ("google", "googleads") and is_paid:
        return "google_paid"
    if is_google_src and not is_paid:
        return "google_organic"
    if not src and not med and not ref:
        return "direct"
    return src or ref or "other"


_SIGNALS = ("klaviyo", "email", "_kx", "klclid")


def _load_from_db(start: str, end: str) -> list[dict]:
    from src.db.supabase_client import SupabaseStore

    store = SupabaseStore()
    res = (store.client.table("orders").select("raw,day_rome")
           .gte("day_rome", start).lte("day_rome", end).limit(10000).execute())
    return [r.get("raw") or {} for r in (res.data or [])]


def main() -> int:
    ap = argparse.ArgumentParser(description="Audit last-click source classification.")
    ap.add_argument("start", help="Giorno inizio YYYY-MM-DD")
    ap.add_argument("end", help="Giorno fine YYYY-MM-DD")
    ap.add_argument("--fixture", help="JSON con lista di ordini grezzi (offline)")
    ap.add_argument("--sample", type=int, default=25, help="Max righe di campione da stampare")
    args = ap.parse_args()

    if args.fixture:
        with open(args.fixture, encoding="utf-8") as fh:
            orders = json.load(fh)
        print(f"(offline) {len(orders)} ordini dal fixture {args.fixture}\n")
    else:
        orders = _load_from_db(args.start, args.end)
        print(f"{len(orders)} ordini da Supabase per {args.start} → {args.end}\n")

    moved_to_email = 0
    internal_to_direct = 0
    internal_old_buckets: Counter = Counter()
    old_dist: Counter = Counter()
    new_dist: Counter = Counter()
    samples: list[tuple] = []

    for o in orders:
        old = _old_classify(o)
        new = classify_order(o)
        old_dist[old] += 1
        new_dist[new] += 1
        if old != "email" and new == "email":
            moved_to_email += 1
        ref = _referrer_domain(o)
        # referrer interno che PRIMA trapelava come sorgente e ORA è direct
        from src.metrics.sales_source import _is_internal_domain
        if _is_internal_domain(ref) and old != "direct" and new == "direct":
            internal_to_direct += 1
            internal_old_buckets[old] += 1

        landing = str(o.get("landing_site") or "")
        referring = str(o.get("referring_site") or "")
        blob = (landing + " " + referring).lower()
        if any(sig in blob for sig in _SIGNALS) and len(samples) < args.sample:
            samples.append((o.get("id") or o.get("order_number") or "?",
                            landing[:90], referring[:60], old, new))

    print(f"=== SAMPLE (contiene {_SIGNALS}) — max {args.sample} ===")
    if not samples:
        print("  (nessun ordine con questi segnali nel range)")
    for oid, landing, referring, old, new in samples:
        moved = "  ← RECLASSIFIED" if old != new else ""
        print(f"  #{oid}\n    landing:   {landing}\n    referring: {referring}\n"
              f"    {old} → {new}{moved}")

    print("\n=== _kx / email findings ===")
    print(f"  ordini riclassificati a EMAIL (grazie a _kx/klclid o email): {moved_to_email}")

    print("\n=== internal referrers (B) ===")
    print(f"  referrer interni ora 'direct' (prima trapelavano): {internal_to_direct}")
    if internal_old_buckets:
        for bucket, n in internal_old_buckets.most_common():
            print(f"    prima classificati come '{bucket}': {n}")

    print("\n=== bucket distribution OLD → NEW ===")
    for bucket in sorted(set(old_dist) | set(new_dist)):
        print(f"  {bucket:<22} old {old_dist.get(bucket, 0):>5}  →  new {new_dist.get(bucket, 0):>5}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
