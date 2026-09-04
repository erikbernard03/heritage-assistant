#!/usr/bin/env python3
"""
Dump GREZZO delle balance transactions Stripe di UN giorno, per capire cosa entra (o entrava
per errore) nel lordo. Mostra per ogni transazione: type, reporting_category, amount (cent e
maggiori), currency, exchange_rate, USD-equivalente e se conta nel gross.

Poi confronta:
  - GROSS "vecchio" (bug): Σ amount/100 dei tipi charge/payment TRATTATI COME USD (nessuna
    conversione di valuta) — riproduce l'inflazione ~6-7×.
  - GROSS "nuovo" (fix): Σ USD-equivalente dei SOLI tipi charge/payment (valuta convertita).

Uso (con chiave reale, es. su Railway o in locale con STRIPE_API_KEY nell'ambiente):
    python -m scripts.stripe_debug_day 2026-09-02
    python scripts/stripe_debug_day.py 2026-09-02

Uso offline (senza rete), passando un fixture JSON = lista di balance transactions grezze:
    python scripts/stripe_debug_day.py 2026-09-02 --fixture path/to/txns.json

Nessun segreto viene stampato.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.metrics.stripe_metrics import (  # noqa: E402
    _GROSS_TYPES,
    _REFUND_TYPES,
    _usd,
    convert_payouts_usd,
    daily_from_balance_transactions,
    settlement_to_usd_rate,
)


def _old_gross(txns: list[dict]) -> float:
    """Logica BUGGATA: amount/100 dei charge/payment come USD, ignorando la valuta."""
    tot = 0.0
    for t in txns:
        if str(t.get("type") or "").lower() in _GROSS_TYPES:
            tot += float(t.get("amount") or 0) / 100.0
    return tot


def _fetch(day: date) -> tuple[list[dict], list[dict]]:
    from datetime import timedelta

    from src.connectors.stripe_conn import StripeConnector

    sc = StripeConnector()
    txns = sc.balance_transactions(day, day)
    pays = sc.payouts(day, day + timedelta(days=10))
    return txns, pays


def main() -> int:
    ap = argparse.ArgumentParser(description="Dump raw Stripe balance transactions for one day.")
    ap.add_argument("day", help="Giorno YYYY-MM-DD (Europe/Rome)")
    ap.add_argument("--fixture", help="JSON con lista di balance transactions grezze (offline)")
    args = ap.parse_args()

    day = date.fromisoformat(args.day)
    payouts: list[dict] = []
    if args.fixture:
        with open(args.fixture, encoding="utf-8") as fh:
            txns = json.load(fh)
        print(f"(offline) {len(txns)} transazioni dal fixture {args.fixture}\n")
    else:
        txns, payouts = _fetch(day)
        print(f"{len(txns)} balance transactions recuperate da Stripe per {day}\n")

    # 1) Dump per transazione.
    hdr = (f"{'type':<16} {'report_cat':<14} {'amount¢':>12} {'major':>12} "
           f"{'cur':>4} {'fx_rate':>9} {'USD':>12}  gross?")
    print(hdr)
    print("-" * len(hdr))
    per_group: dict[tuple, dict] = defaultdict(lambda: {"n": 0, "major": 0.0, "usd": 0.0})
    for t in sorted(txns, key=lambda x: str(x.get("type"))):
        typ = str(t.get("type") or "").lower()
        cur = str(t.get("currency") or "usd").lower()
        er = t.get("exchange_rate")
        amount = t.get("amount")
        major = float(amount or 0) / 100.0
        usd = _usd(amount, cur, er)
        in_gross = "YES" if typ in _GROSS_TYPES else ("refund" if typ in _REFUND_TYPES else "no")
        er_s = f"{float(er):.5f}" if er not in (None, "") else "—"
        print(f"{typ:<16} {str(t.get('reporting_category') or '—'):<14} "
              f"{float(amount or 0):>12,.0f} {major:>12,.2f} {cur:>4} {er_s:>9} "
              f"{usd:>12,.2f}  {in_gross}")
        g = per_group[(typ, cur)]
        g["n"] += 1
        g["major"] += major
        g["usd"] += usd

    # 2) Subtotali per (type, currency).
    print("\nSubtotali per (type, currency):")
    print(f"  {'type':<16} {'cur':>4} {'n':>5} {'Σ major':>14} {'Σ USD':>14}")
    for (typ, cur), g in sorted(per_group.items()):
        print(f"  {typ:<16} {cur:>4} {g['n']:>5} {g['major']:>14,.2f} {g['usd']:>14,.2f}")

    # 3) OLD vs NEW gross.
    old_g = _old_gross(txns)
    agg = daily_from_balance_transactions(txns).get(day.isoformat(), {})
    new_g = float(agg.get("gross") or 0)
    rate = settlement_to_usd_rate(txns)
    print("\n=== GROSS ===")
    print(f"  OLD (bug, no FX, charge+payment as USD): ${old_g:,.2f}")
    print(f"  NEW (fix, FX-converted, charge/payment): ${new_g:,.2f}")
    if new_g:
        print(f"  ratio OLD/NEW: {old_g / new_g:.3f}x")
    print(f"  fee ${float(agg.get('fee') or 0):,.2f} · net ${float(agg.get('net') or 0):,.2f} · "
          f"charges {int(agg.get('charge_count') or 0)} · "
          f"refunds ${float(agg.get('refund_amount') or 0):,.2f}")
    print(f"  effective settlement→USD rate: {rate:.5f}")

    # 4) Payouts (se recuperati dal vivo).
    if payouts:
        conv = convert_payouts_usd(payouts, rate)
        print("\n=== PAYOUTS (arrivo entro +10g) ===")
        for raw, c in zip(payouts, conv):
            print(f"  {raw.get('arrival_date')}: native {float(raw.get('amount') or 0):,.2f} "
                  f"{str(raw.get('currency') or 'usd').upper()} → ${float(c.get('amount') or 0):,.2f} "
                  f"· {raw.get('status')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
