"""
Calcoli deterministici del net profit (codice puro — MAI AI sui numeri).

Formula net profit giornaliero (Fase 1, solo Shopify => spesa ads = 0):

    net_profit =
        revenue_reale_shopify
      − COGS_totale            (somma per line item via handle; $0/sconosciuto = $3)
      − costi_spedizione       ($7 × numero_ordini)
      − fee_pagamenti          (7.5% × revenue)
      − spesa_ads_totale       (Meta+Google+TikTok; in Fase 1 = 0)
      − quota_costi_fissi      ($5.668 / 30 ≈ $188.93/giorno)  [attivabile/disattivabile]

Si calcolano sia il net profit "operativo" (senza costi fissi) sia quello
"netto" (con la quota costi fissi).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from config import settings
from src.config_loader import CogsResolver, get_resolver


def _to_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _is_cancelled(order: dict) -> bool:
    return bool(order.get("cancelled_at"))


@dataclass
class LineItemCost:
    order_id: int
    line_item_id: Optional[int]
    product_id: Optional[int]
    handle: Optional[str]
    title: str
    sku: Optional[str]
    quantity: int
    unit_cogs: float
    line_cogs: float


@dataclass
class DailyMetrics:
    day: str                      # YYYY-MM-DD (Europe/Rome) di riferimento
    num_orders: int = 0
    revenue: float = 0.0
    cogs_total: float = 0.0
    shipping_total: float = 0.0
    payment_fees: float = 0.0
    ads_spend: float = 0.0        # Fase 1 Shopify: 0
    fixed_cost_daily: float = 0.0
    net_profit_operativo: float = 0.0   # senza costi fissi
    net_profit_netto: float = 0.0       # con costi fissi
    aov: float = 0.0
    line_items: list[LineItemCost] = field(default_factory=list)

    def as_db_row(self) -> dict:
        """Riga per la tabella daily_metrics (tutto in USD)."""
        return {
            "day": self.day,
            "num_orders": self.num_orders,
            "revenue": round(self.revenue, 2),
            "cogs_total": round(self.cogs_total, 2),
            "shipping_total": round(self.shipping_total, 2),
            "payment_fees": round(self.payment_fees, 2),
            "ads_spend": round(self.ads_spend, 2),
            "fixed_cost_daily": round(self.fixed_cost_daily, 2),
            "net_profit_operativo": round(self.net_profit_operativo, 2),
            "net_profit_netto": round(self.net_profit_netto, 2),
            "aov": round(self.aov, 2),
        }


def compute_daily_metrics(
    day: str,
    orders: list[dict],
    handle_map: dict[int, str],
    resolver: Optional[CogsResolver] = None,
    ads_spend: float = 0.0,
) -> DailyMetrics:
    """
    Calcola le metriche del giorno a partire dagli ordini Shopify.

    - `orders`: ordini Shopify (con line_items) creati nel giorno.
    - `handle_map`: product_id -> handle (per risolvere il COGS per handle).
    - `ads_spend`: spesa pubblicitaria totale in USD (Fase 1 = 0).
    Gli ordini cancellati sono esclusi da revenue/conteggio.
    """
    resolver = resolver or get_resolver()
    m = DailyMetrics(day=day)

    for order in orders:
        if _is_cancelled(order):
            continue

        order_id = int(order.get("id", 0))
        revenue = _to_float(order.get("total_price"))
        m.revenue += revenue
        m.num_orders += 1

        for li in order.get("line_items", []):
            product_id = li.get("product_id")
            handle = handle_map.get(int(product_id)) if product_id else None
            title = li.get("title") or ""
            qty = int(li.get("quantity", 1) or 1)
            unit_cogs = resolver.cogs_for_handle(handle, title)
            line_cogs = unit_cogs * qty
            m.cogs_total += line_cogs
            m.line_items.append(
                LineItemCost(
                    order_id=order_id,
                    line_item_id=li.get("id"),
                    product_id=int(product_id) if product_id else None,
                    handle=handle,
                    title=title,
                    sku=li.get("sku"),
                    quantity=qty,
                    unit_cogs=unit_cogs,
                    line_cogs=line_cogs,
                )
            )

    m.shipping_total = settings.SPEDIZIONE_PER_ORDINE * m.num_orders
    m.payment_fees = settings.FEE_PAGAMENTI * m.revenue
    m.ads_spend = ads_spend

    if settings.INCLUDI_COSTI_FISSI_IN_NET_PROFIT:
        m.fixed_cost_daily = (
            settings.COSTI_FISSI_MENSILI / settings.GIORNI_MESE_ALLOCAZIONE
        )

    m.net_profit_operativo = (
        m.revenue - m.cogs_total - m.shipping_total - m.payment_fees - m.ads_spend
    )
    m.net_profit_netto = m.net_profit_operativo - m.fixed_cost_daily
    m.aov = (m.revenue / m.num_orders) if m.num_orders else 0.0

    return m
