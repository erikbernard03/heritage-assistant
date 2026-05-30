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

    # ----------------------------------------------------- letture (read-only)
    def get_recent_daily_metrics(self, days: int = 14) -> list[dict]:
        """Ultime N righe di daily_metrics (più recenti prima)."""
        res = (
            self.client.table("daily_metrics")
            .select("*")
            .order("day", desc=True)
            .limit(days)
            .execute()
        )
        return res.data or []

    def get_daily_metrics_for_day(self, day: str) -> Optional[dict]:
        """Riga di metriche per un giorno specifico (YYYY-MM-DD), se esiste."""
        res = (
            self.client.table("daily_metrics")
            .select("*")
            .eq("day", day)
            .limit(1)
            .execute()
        )
        return (res.data or [None])[0]

    def get_daily_metrics_range(self, start_day: str, end_day: str) -> list[dict]:
        """Righe di metriche nell'intervallo [start_day, end_day] inclusi."""
        res = (
            self.client.table("daily_metrics")
            .select("*")
            .gte("day", start_day)
            .lte("day", end_day)
            .order("day", desc=False)
            .execute()
        )
        return res.data or []

    # ----------------------------------------------------------- Meta (Fase 2)
    def upsert_meta_daily(self, meta) -> None:
        """Upsert dei totali Meta giornalieri (chiave = day)."""
        self.client.table("meta_daily").upsert(meta.as_db_row()).execute()

    def upsert_meta_campaigns(self, meta) -> int:
        """Upsert del breakdown per campagna (chiave = day+campaign_id)."""
        rows = [c.as_db_row() for c in meta.campaigns if c.campaign_id]
        if not rows:
            return 0
        self.client.table("meta_campaigns").upsert(rows).execute()
        return len(rows)

    def get_meta_daily_for_day(self, day: str) -> Optional[dict]:
        """Totali Meta per un giorno specifico, se già presenti (cache 1-call/giorno)."""
        res = (
            self.client.table("meta_daily")
            .select("*")
            .eq("day", day)
            .limit(1)
            .execute()
        )
        return (res.data or [None])[0]

    def get_meta_campaigns_for_day(self, day: str) -> list[dict]:
        """Breakdown campagne Meta per un giorno (ordinate per spesa decrescente)."""
        res = (
            self.client.table("meta_campaigns")
            .select("*")
            .eq("day", day)
            .order("spend", desc=True)
            .execute()
        )
        return res.data or []

    def get_recent_meta_daily(self, days: int = 14) -> list[dict]:
        """Ultime N righe di meta_daily (più recenti prima)."""
        res = (
            self.client.table("meta_daily")
            .select("*")
            .order("day", desc=True)
            .limit(days)
            .execute()
        )
        return res.data or []

    def get_recent_meta_campaigns(self, days: int = 7, limit: int = 60) -> list[dict]:
        """Campagne Meta recenti (per dare contesto all'assistente AI)."""
        res = (
            self.client.table("meta_campaigns")
            .select("*")
            .order("day", desc=True)
            .order("spend", desc=True)
            .limit(limit)
            .execute()
        )
        return res.data or []

    # -------------------------------------------------------- Klaviyo (Fase 4)
    def upsert_klaviyo_daily(self, kla) -> None:
        """Upsert dei totali Klaviyo (campagne) giornalieri (chiave = day)."""
        self.client.table("klaviyo_daily").upsert(kla.as_db_row()).execute()

    def upsert_klaviyo_campaigns(self, kla) -> int:
        """Upsert del breakdown per campagna (chiave = day+campaign_id)."""
        rows = [c.as_db_row() for c in kla.campaigns if c.campaign_id]
        if not rows:
            return 0
        self.client.table("klaviyo_campaigns").upsert(rows).execute()
        return len(rows)

    def get_klaviyo_daily_for_day(self, day: str) -> Optional[dict]:
        """Totali Klaviyo per un giorno, se presenti (cache 1-call/giorno)."""
        res = (
            self.client.table("klaviyo_daily")
            .select("*")
            .eq("day", day)
            .limit(1)
            .execute()
        )
        return (res.data or [None])[0]

    def get_klaviyo_campaigns_for_day(self, day: str) -> list[dict]:
        """Breakdown campagne Klaviyo per un giorno (ordinate per revenue desc)."""
        res = (
            self.client.table("klaviyo_campaigns")
            .select("*")
            .eq("day", day)
            .order("revenue", desc=True)
            .execute()
        )
        return res.data or []

    def get_recent_klaviyo_daily(self, days: int = 14) -> list[dict]:
        """Ultime N righe di klaviyo_daily (più recenti prima)."""
        res = (
            self.client.table("klaviyo_daily")
            .select("*")
            .order("day", desc=True)
            .limit(days)
            .execute()
        )
        return res.data or []

    def get_recent_klaviyo_campaigns(self, days: int = 7, limit: int = 60) -> list[dict]:
        """Campagne Klaviyo recenti (per dare contesto all'assistente AI)."""
        res = (
            self.client.table("klaviyo_campaigns")
            .select("*")
            .order("day", desc=True)
            .order("revenue", desc=True)
            .limit(limit)
            .execute()
        )
        return res.data or []


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
