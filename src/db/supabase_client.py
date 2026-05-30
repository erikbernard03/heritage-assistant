"""
Wrapper Supabase per persistere ordini, line items e metriche giornaliere.

Tutti i valori monetari sono in USD. Le scritture usano upsert idempotente
(richiamabili più volte sullo stesso giorno senza duplicare).
"""
from __future__ import annotations

from typing import Optional

from config import settings
from src.metrics.profit import DailyMetrics


class SupabaseStore:
    def __init__(self, url: Optional[str] = None, key: Optional[str] = None):
        self.url = url or settings.SUPABASE_URL
        self.key = key or settings.SUPABASE_KEY
        if not (self.url and self.key):
            raise RuntimeError("SUPABASE_URL / SUPABASE_KEY non configurati (.env).")
        # import lazy: evita di richiedere il pacchetto se Supabase non si usa
        from supabase import create_client

        self.client = create_client(self.url, self.key)

    # ----------------------------------------------------------------- orders
    def upsert_orders(self, orders: list[dict], handle_map: dict[int, str]) -> int:
        """Upsert degli ordini Shopify nella tabella `orders`."""
        rows = []
        for o in orders:
            line_items = o.get("line_items", [])
            rows.append(
                {
                    "id": int(o["id"]),
                    "order_number": str(o.get("order_number") or o.get("name") or ""),
                    "created_at": o.get("created_at"),
                    "processed_at": o.get("processed_at"),
                    "cancelled_at": o.get("cancelled_at"),
                    "financial_status": o.get("financial_status"),
                    "fulfillment_status": o.get("fulfillment_status"),
                    "currency": o.get("currency"),
                    "total_price": _num(o.get("total_price")),
                    "subtotal_price": _num(o.get("subtotal_price")),
                    "total_tax": _num(o.get("total_tax")),
                    "total_shipping": _shipping(o),
                    "num_line_items": len(line_items),
                    "day_rome": o.get("_day_rome"),
                    "raw": o,
                }
            )
        if not rows:
            return 0
        self.client.table("orders").upsert(rows).execute()
        return len(rows)

    # ------------------------------------------------------------- line items
    def upsert_line_items(self, metrics: DailyMetrics) -> int:
        """Upsert dei line items con il COGS applicato (da DailyMetrics)."""
        rows = []
        for li in metrics.line_items:
            if li.line_item_id is None:
                continue
            rows.append(
                {
                    "id": int(li.line_item_id),
                    "order_id": li.order_id,
                    "product_id": li.product_id,
                    "handle": li.handle,
                    "title": li.title,
                    "sku": li.sku,
                    "quantity": li.quantity,
                    "unit_cogs": round(li.unit_cogs, 2),
                    "line_cogs": round(li.line_cogs, 2),
                }
            )
        if not rows:
            return 0
        self.client.table("line_items").upsert(rows).execute()
        return len(rows)

    # ---------------------------------------------------------- daily metrics
    def upsert_daily_metrics(self, metrics: DailyMetrics) -> None:
        """Upsert della riga giornaliera (chiave = day)."""
        self.client.table("daily_metrics").upsert(metrics.as_db_row()).execute()


def _num(value) -> float:
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return 0.0


def _shipping(order: dict) -> float:
    """Spedizione pagata dal cliente (somma delle shipping_lines)."""
    total = 0.0
    for line in order.get("shipping_lines", []) or []:
        total += _num(line.get("price"))
    return round(total, 2)
