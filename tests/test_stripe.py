"""
Test deterministici delle aggregazioni Stripe + refund Shopify. Nessuna rete.
Gli importi Stripe grezzi sono in CENTESIMI (come li restituisce l'API).
"""
from datetime import datetime

import pytz

from src.metrics.stripe_metrics import (
    daily_from_balance_transactions,
    dispute_rate,
    fee_rate,
    payouts_monthly,
    reconciliation_row,
    refunds_from_orders,
    refunds_monthly,
    stripe_monthly,
    total_payment_cost_rate,
)


def _unix(day, hour=12):
    # unix UTC di un'ora del giorno; la conversione a Roma avviene nella funzione
    return int(pytz.utc.localize(datetime(*[int(x) for x in day.split("-")], hour)).timestamp())


def test_daily_from_balance_transactions_gross_fee_net_refund():
    txns = [
        {"created": _unix("2026-08-19"), "type": "charge", "amount": 10000, "fee": 320},  # $100, fee $3.20
        {"created": _unix("2026-08-19"), "type": "payment", "amount": 5000, "fee": 175},   # $50, fee $1.75
        {"created": _unix("2026-08-19"), "type": "refund", "amount": -2000, "fee": 0},     # -$20
        {"created": _unix("2026-08-19"), "type": "payout", "amount": -8000, "fee": 0},      # ignorato nel gross
    ]
    d = daily_from_balance_transactions(txns)["2026-08-19"]
    assert round(d["gross"], 2) == 150.0
    assert round(d["fee"], 2) == 4.95
    assert d["charge_count"] == 2
    assert round(d["refund_amount"], 2) == 20.0 and d["refund_count"] == 1
    # net = gross − fee − refund = 150 − 4.95 − 20 = 125.05
    assert round(d["net"], 2) == 125.05


def test_daily_buckets_by_rome_day():
    # 23:30 UTC del 18 agosto = 01:30 Roma del 19 (CEST +2) -> giorno Roma 08-19
    ts = int(pytz.utc.localize(datetime(2026, 8, 18, 23, 30)).timestamp())
    out = daily_from_balance_transactions([{"created": ts, "type": "charge", "amount": 1000, "fee": 30}])
    assert "2026-08-19" in out and out["2026-08-19"]["charge_count"] == 1


def test_fee_rate_and_dispute_rate():
    assert round(fee_rate(1000.0, 75.0), 4) == 0.075
    assert fee_rate(0.0, 5.0) is None
    assert round(dispute_rate(3, 1000), 4) == 0.003
    assert dispute_rate(1, 0) is None


def test_total_payment_cost_rate_stripe_plus_surcharge():
    # Stripe fee reale 6.21% (gross 10000, fee 621) + surcharge Shopify 2% = totale 8.21%.
    r = total_payment_cost_rate(10000.0, 621.0, surcharge_pct=0.02)
    assert round(r["stripe_rate"], 4) == 0.0621
    assert r["surcharge_rate"] == 0.02
    assert round(r["total_rate"], 4) == 0.0821
    # Confronto col 7.5%: la sola fee Stripe sembra sotto, ma il TOTALE lo supera.
    assert r["stripe_rate"] < 0.075 < r["total_rate"]


def test_total_payment_cost_rate_zero_gross_and_default_surcharge():
    # gross 0 -> stripe/total None, ma surcharge sempre riportato.
    r0 = total_payment_cost_rate(0.0, 5.0, surcharge_pct=0.02)
    assert r0["stripe_rate"] is None and r0["total_rate"] is None and r0["surcharge_rate"] == 0.02
    # surcharge None -> usa il default da settings (0.0 finché non configurato).
    r1 = total_payment_cost_rate(1000.0, 62.0)
    assert r1["surcharge_rate"] == 0.0 and round(r1["total_rate"], 4) == 0.062


def test_reconciliation_row_diff():
    rc = reconciliation_row(shopify_revenue=10000.0, stripe_gross=8500.0,
                            stripe_net=8000.0, payouts_amount=7800.0)
    assert rc["diff"] == -1500.0               # Stripe gross < Shopify (quota PayPal)
    assert round(rc["diff_pct"], 1) == -15.0


def test_stripe_and_payouts_monthly():
    rows = [
        {"day": "2026-07-31", "gross_amount": 100.0, "fee_amount": 3.0, "net_amount": 97.0,
         "charge_count": 1, "refund_amount": 0.0, "refund_count": 0},
        {"day": "2026-08-01", "gross_amount": 200.0, "fee_amount": 6.0, "net_amount": 194.0,
         "charge_count": 2, "refund_amount": 10.0, "refund_count": 1},
    ]
    sm = stripe_monthly(rows)
    assert sm["2026-08"]["gross"] == 200.0 and sm["2026-08"]["charge_count"] == 2
    pays = [{"arrival_date": "2026-08-05", "amount": 150.0, "status": "paid"},
            {"arrival_date": "2026-08-20", "amount": 50.0, "status": "in_transit"}]
    assert payouts_monthly(pays)["2026-08"] == 200.0


def test_refunds_from_orders_and_monthly():
    orders = [
        {"_day_rome": "2026-08-19", "refunds": [
            {"transactions": [{"amount": "20.00"}, {"amount": "5.00"}]},
            {"transactions": [{"amount": "10.00"}]},
        ]},
        {"_day_rome": "2026-08-19", "refunds": []},   # nessun refund
    ]
    by = refunds_from_orders(orders)
    assert by["2026-08-19"] == {"amount": 35.0, "count": 2}
    rm = refunds_monthly([
        {"day": "2026-08-01", "refund_amount": 35.0, "refund_count": 2},
        {"day": "2026-08-10", "refund_amount": 15.0, "refund_count": 1},
    ])
    assert rm["2026-08"] == {"amount": 50.0, "count": 3}
