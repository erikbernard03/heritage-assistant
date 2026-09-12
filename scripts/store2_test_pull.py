#!/usr/bin/env python3
"""
Test pull dal SECONDO store Shopify (heritagering.co) — SOLA LETTURA, nessuna scrittura DB.

Verifica: connessione/auth, valuta base dello store, scope concessi, un pull di ordini per un
giorno, e la conversione revenue → USD (mostra per un campione: presentment vs shop_money vs
current_total_price e l'importo USD calcolato). Sonda anche le sessioni.

Uso (con le env var del secondo store impostate):
    python -m scripts.store2_test_pull 2026-09-11
    python scripts/store2_test_pull.py 2026-09-11
Offline (fixture = lista di ordini grezzi):
    python scripts/store2_test_pull.py 2026-09-11 --fixture orders.json --currency USD
Nessun segreto in output.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import settings  # noqa: E402
from src.metrics.profit import order_revenue, order_revenue_usd  # noqa: E402


def _presentment(o: dict) -> str:
    s = o.get("current_total_price_set") or o.get("total_price_set") or {}
    pm = s.get("presentment_money") or {}
    return f"{pm.get('amount')} {pm.get('currency_code')}" if pm else "—"


def _shop(o: dict) -> str:
    s = o.get("current_total_price_set") or o.get("total_price_set") or {}
    sm = s.get("shop_money") or {}
    return f"{sm.get('amount')} {sm.get('currency_code')}" if sm else "—"


def main() -> int:
    ap = argparse.ArgumentParser(description="Test pull from the second Shopify store (.co).")
    ap.add_argument("day", help="Giorno YYYY-MM-DD (Europe/Rome)")
    ap.add_argument("--fixture", help="JSON con lista ordini grezzi (offline)")
    ap.add_argument("--currency", help="Valuta base dello store per il test offline (es. USD)")
    args = ap.parse_args()

    fx = settings.SHOPIFY_STORE_2_CURRENCY_TO_USD
    shop_ccy = args.currency

    if args.fixture:
        with open(args.fixture, encoding="utf-8") as fh:
            orders = json.load(fh)
        print(f"(offline) {len(orders)} ordini dal fixture; valuta base assunta: {shop_ccy or '?'} "
              f"· currency_to_usd={fx}\n")
        sessions = None
    else:
        from datetime import time as _t, timedelta

        import pytz

        from src.connectors.shopify import ShopifyConnector

        sc = ShopifyConnector.for_store_2()
        if sc is None:
            print("❌ SHOPIFY_STORE_2 non configurato: imposta le env var del secondo store.")
            return 1
        print(f"Store: {sc.store}  (label '{sc.store_label}')  · currency_to_usd={sc.currency_to_usd}")
        shop_ccy = sc.shop_currency()
        print(f"Shop base currency (from /shop.json): {shop_ccy}")
        if shop_ccy and shop_ccy != "USD" and abs(sc.currency_to_usd - 1.0) < 1e-9:
            print("  ⚠️ valuta base ≠ USD ma SHOPIFY_STORE_2_CURRENCY_TO_USD=1.0 — imposta il tasso!")
        print(f"Granted scopes: {', '.join(sc.get_granted_scopes()) or '(none)'}")

        tz = pytz.timezone(settings.TIMEZONE)
        d = date.fromisoformat(args.day)
        start = tz.localize(__import__('datetime').datetime.combine(d, _t.min))
        end = start + timedelta(days=1)
        orders = sc.get_orders(start, end)
        print(f"\nOrders on {args.day}: {len(orders)}")
        sessions, err, _ = sc.get_sessions_debug(args.day)
        print(f"Sessions probe: {sessions if sessions is not None else 'n/a'}"
              + (f"  (error: {err})" if err else ""))
        fx = sc.currency_to_usd

    # Conversione revenue → USD su un campione.
    print("\n— Sample orders (presentment → shop_money → USD) —")
    total_usd = 0.0
    for o in orders:
        if o.get("cancelled_at"):
            continue
        usd = order_revenue_usd(o, fx)
        total_usd += usd
    for o in [x for x in orders if not x.get("cancelled_at")][:8]:
        print(f"  #{o.get('id') or o.get('order_number') or '?'}: "
              f"presentment {_presentment(o)} · shop {_shop(o)} · "
              f"current_total_price {o.get('current_total_price')} → USD ${order_revenue_usd(o, fx):,.2f}")
    n = len([x for x in orders if not x.get('cancelled_at')])
    print(f"\nTotal revenue (USD, non-cancelled, ×{fx}): ${total_usd:,.2f}  over {n} orders")
    print("NB: se shop_money è già in USD, Shopify ha convertito il presentment; fx resta 1.0.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
