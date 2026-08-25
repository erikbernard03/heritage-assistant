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

        # auto_refresh_token=False evita il thread di refresh in background (NON-daemon)
        # che terrebbe vivo il processo del cron dopo il lavoro; timeouts bounded sulle
        # chiamate REST per non restare appesi.
        client_kwargs = {}
        try:
            from supabase import ClientOptions

            client_kwargs["options"] = ClientOptions(
                auto_refresh_token=False,
                persist_session=False,
                postgrest_client_timeout=30,
                storage_client_timeout=30,
            )
        except Exception:  # noqa: BLE001 — se ClientOptions non disponibile, fallback
            pass
        self.client = create_client(self.url, self.key, **client_kwargs)

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

    # -------------------------------------------------- product units (Fase 5)
    def upsert_product_units(self, day: str, units_by_key: dict[str, int]) -> int:
        """
        Riscrive le unità vendute per prodotto del giorno `day`. DELETE-then-INSERT perché
        l'insieme dei bucket (product_key) può CAMBIARE tra run: es. i vecchi record 'other'
        vengono sostituiti dai titoli reali. Un semplice upsert lascerebbe righe stale.
        units_by_key: {bucket: units} (bucket = chiave famiglia o titolo prodotto).
        """
        self.client.table("product_units_daily").delete().eq("day", day).execute()
        rows = [
            {"day": day, "product_key": key, "units": int(units)}
            for key, units in (units_by_key or {}).items()
            if int(units) != 0
        ]
        if not rows:
            return 0
        self.client.table("product_units_daily").insert(rows).execute()
        return len(rows)

    # ----------------------------------------------- sales by country (Fase 6)
    def upsert_sales_by_country(self, day: str, by_country: dict[str, dict]) -> int:
        """
        Riscrive le vendite per paese del giorno `day` (DELETE-then-INSERT: l'insieme dei
        paesi può cambiare tra run). by_country: {country: {revenue, orders}}.
        """
        self.client.table("sales_by_country_daily").delete().eq("day", day).execute()
        rows = [
            {"day": day, "country": c, "revenue": round(float(v.get("revenue") or 0), 2),
             "orders": int(v.get("orders") or 0)}
            for c, v in (by_country or {}).items()
            if int(v.get("orders") or 0) != 0
        ]
        if not rows:
            return 0
        self.client.table("sales_by_country_daily").insert(rows).execute()
        return len(rows)

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

    def get_daily_metrics_before(self, day: str, limit: int = 4) -> list[dict]:
        """
        Le ultime `limit` righe di daily_metrics con day < `day` (le più recenti prima).
        Robusto ai buchi: salta i giorni mancanti e prende N giorni REALI.
        """
        res = (
            self.client.table("daily_metrics")
            .select("*")
            .lt("day", day)
            .order("day", desc=True)
            .limit(limit)
            .execute()
        )
        return res.data or []

    _RANGE_TABLES = {
        "daily_metrics", "meta_daily", "meta_campaigns", "tiktok_daily",
        "tiktok_campaigns", "google_daily", "klaviyo_daily", "klaviyo_campaigns",
        "product_units_daily", "sales_by_country_daily",
    }

    def get_table_range(self, table: str, start_day: str, end_day: str) -> list[dict]:
        """Tutte le righe di `table` con day in [start_day, end_day] (per i report multi-giorno)."""
        if table not in self._RANGE_TABLES:
            raise ValueError(f"table non consentita: {table}")
        res = (
            self.client.table(table)
            .select("*")
            .gte("day", start_day)
            .lte("day", end_day)
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

    # --------------------------------------------------------- TikTok (Fase 3)
    def upsert_tiktok_daily(self, tt) -> None:
        """Upsert dei totali TikTok giornalieri (chiave = day)."""
        self.client.table("tiktok_daily").upsert(tt.as_db_row()).execute()

    def upsert_tiktok_campaigns(self, tt) -> int:
        """Upsert del breakdown per campagna (chiave = day+campaign_id)."""
        rows = [c.as_db_row() for c in tt.campaigns if c.campaign_id]
        if not rows:
            return 0
        self.client.table("tiktok_campaigns").upsert(rows).execute()
        return len(rows)

    def get_tiktok_daily_for_day(self, day: str) -> Optional[dict]:
        """Totali TikTok per un giorno, se presenti (cache 1-call/giorno)."""
        res = (
            self.client.table("tiktok_daily")
            .select("*")
            .eq("day", day)
            .limit(1)
            .execute()
        )
        return (res.data or [None])[0]

    def get_tiktok_campaigns_for_day(self, day: str) -> list[dict]:
        """Breakdown campagne TikTok per un giorno (ordinate per spesa decrescente)."""
        res = (
            self.client.table("tiktok_campaigns")
            .select("*")
            .eq("day", day)
            .order("spend", desc=True)
            .execute()
        )
        return res.data or []

    def get_recent_tiktok_daily(self, days: int = 14) -> list[dict]:
        """Ultime N righe di tiktok_daily (più recenti prima)."""
        res = (
            self.client.table("tiktok_daily")
            .select("*")
            .order("day", desc=True)
            .limit(days)
            .execute()
        )
        return res.data or []

    def get_recent_tiktok_campaigns(self, days: int = 7, limit: int = 60) -> list[dict]:
        """Campagne TikTok recenti (per dare contesto all'assistente AI)."""
        res = (
            self.client.table("tiktok_campaigns")
            .select("*")
            .order("day", desc=True)
            .order("spend", desc=True)
            .limit(limit)
            .execute()
        )
        return res.data or []

    # --------------------------------------------------------- Google (Fase 2)
    def upsert_google_daily(self, g) -> None:
        """Upsert dei totali Google giornalieri (chiave = day)."""
        self.client.table("google_daily").upsert(g.as_db_row()).execute()

    def get_google_daily_for_day(self, day: str) -> Optional[dict]:
        """Totali Google per un giorno, se presenti (cache 1-call/giorno)."""
        res = (
            self.client.table("google_daily")
            .select("*")
            .eq("day", day)
            .limit(1)
            .execute()
        )
        return (res.data or [None])[0]

    def get_recent_google_daily(self, days: int = 14) -> list[dict]:
        """Ultime N righe di google_daily (più recenti prima)."""
        res = (
            self.client.table("google_daily")
            .select("*")
            .order("day", desc=True)
            .limit(days)
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
