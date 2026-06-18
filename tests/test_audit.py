"""
Test deterministici dell'aggregazione audit (summarize_orders) — nessuna rete.
"""
from src.diagnostics import summarize_orders


def _order(num, total, subtotal, tax, shipping, current=None, status="paid",
           cancelled=False, created="2026-06-17T12:00:00Z"):
    o = {
        "order_number": num,
        "total_price": str(total),
        "subtotal_price": str(subtotal),
        "total_tax": str(tax),
        "total_shipping_price_set": {"shop_money": {"amount": str(shipping)}},
        "financial_status": status,
        "created_at": created,
        "cancelled_at": "2026-06-17T13:00:00Z" if cancelled else None,
    }
    if current is not None:
        o["current_total_price"] = str(current)
    return o


def test_summarize_orders_vat_split():
    # 2 ordini: total = sub + tax + ship
    orders = [
        _order(1, total=121.0, subtotal=100.0, tax=15.0, shipping=6.0),
        _order(2, total=60.5, subtotal=50.0, tax=7.5, shipping=3.0),
    ]
    s = summarize_orders(orders)
    assert s["n_active"] == 2 and s["n_cancelled"] == 0
    assert round(s["total_price"], 2) == 181.50
    assert round(s["subtotal"], 2) == 150.0
    assert round(s["tax"], 2) == 22.5
    assert round(s["shipping"], 2) == 9.0
    # revenue − tax = subtotal + shipping
    assert round(s["total_price"] - s["tax"], 2) == round(s["subtotal"] + s["shipping"], 2)
    assert s["refunded_amount"] == 0.0


def test_summarize_orders_refund_and_cancelled_excluded():
    orders = [
        _order(1, total=200.0, subtotal=180.0, tax=15.0, shipping=5.0,
               current=150.0, status="partially_refunded"),       # refund 50
        _order(2, total=100.0, subtotal=90.0, tax=8.0, shipping=2.0),
        _order(3, total=999.0, subtotal=900.0, tax=80.0, shipping=19.0, cancelled=True),
    ]
    s = summarize_orders(orders)
    assert s["n_active"] == 2 and s["n_cancelled"] == 1
    assert round(s["total_price"], 2) == 300.0          # cancelled escluso
    assert round(s["current_total"], 2) == 250.0        # 150 + 100
    assert round(s["refunded_amount"], 2) == 50.0       # report conta il total_price, non il current
    assert len(s["refunded_orders"]) == 1
    assert round(s["cancelled_total"], 2) == 999.0
