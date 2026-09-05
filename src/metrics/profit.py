"""
Calcoli deterministici del net profit (codice puro — MAI AI sui numeri).

Formula net profit giornaliero (Fase 1, solo Shopify => spesa ads = 0):

    net_profit =
        revenue_reale_shopify
      − COGS_totale            (somma per line item via handle; $0/sconosciuto = $3)
      − costi_spedizione       ($7 × numero_ordini)
      − fee_pagamenti          (7.5% × revenue)
      − spesa_ads_totale       (Meta+Google+TikTok; in Fase 1 = 0)
      − quota_costi_fissi      ($6.117 / 30 ≈ $203.90/giorno)  [attivabile/disattivabile]

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


def order_revenue(order: dict) -> float:
    """
    Revenue di un ordine = `current_total_price` (totale ATTUALE, già al netto dei rimborsi
    dell'ordine stesso), datato sul GIORNO DELL'ORDINE. È la nostra definizione stabilita:
    contabilità per data-ordine, NON per data-rimborso. Fallback a `total_price` (lordo) solo
    se `current_total_price` è assente (API vecchia).
    """
    v = order.get("current_total_price")
    if v in (None, ""):
        v = order.get("total_price")
    return _to_float(v)


def _is_cancelled(order: dict) -> bool:
    return bool(order.get("cancelled_at"))


def _order_shipping_collected(order: dict) -> float:
    """Spedizione CARICATA al cliente (income). total_shipping_price_set, fallback shipping_lines."""
    s = order.get("total_shipping_price_set")
    if isinstance(s, dict):
        amt = (s.get("shop_money") or {}).get("amount")
        if amt is not None:
            return _to_float(amt)
    total = 0.0
    for line in order.get("shipping_lines") or []:
        total += _to_float(line.get("price"))
    return total


def _order_tax_collected(order: dict) -> float:
    """IVA/tasse incassate dal cliente (income)."""
    return _to_float(order.get("total_tax"))


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
    # Income già INCLUSO in `revenue` (total_price), separato per visibilità (no double count):
    shipping_collected: float = 0.0     # spedizione premium pagata dal cliente
    tax_collected: float = 0.0          # IVA/tasse incassate
    store_cvr: float = 0.0              # CVR negozio (Shopify primario, TW fallback); frazione
    store_sessions: Optional[int] = None  # sessioni/visitatori reali Shopify (None se non disp.)
    line_items: list[LineItemCost] = field(default_factory=list)

    @property
    def product_revenue(self) -> float:
        """Revenue di solo prodotto = total_price − spedizione incassata − IVA incassata."""
        return self.revenue - self.shipping_collected - self.tax_collected

    def as_db_row(self) -> dict:
        """Riga per la tabella daily_metrics (tutto in USD)."""
        row = {
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
            "store_cvr": round(self.store_cvr, 6),
        }
        # Colonna nullable (migration 008): la includo solo se disponibile, così i giorni
        # senza sessioni reali non scrivono NULL e restano compatibili pre-migration.
        if self.store_sessions is not None:
            row["store_sessions"] = int(self.store_sessions)
        return row


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
        # Revenue = current_total_price (netto dei rimborsi propri, datato sull'ordine).
        revenue = order_revenue(order)
        m.revenue += revenue
        # spedizione + IVA sono GIÀ dentro total_price: le separiamo solo per mostrarle
        m.shipping_collected += _order_shipping_collected(order)
        m.tax_collected += _order_tax_collected(order)
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
        # Quota giornaliera DATATA: usa il costo fisso mensile in vigore al giorno `day`.
        from src.metrics.fixed_costs import daily_fixed_allocation

        m.fixed_cost_daily = daily_fixed_allocation(day)

    m.net_profit_operativo = (
        m.revenue - m.cogs_total - m.shipping_total - m.payment_fees - m.ads_spend
    )
    m.net_profit_netto = m.net_profit_operativo - m.fixed_cost_daily
    m.aov = (m.revenue / m.num_orders) if m.num_orders else 0.0

    return m


def compute_breakeven_full(prev_days_rows: list[dict]) -> dict:
    """
    Break-even CONTRIBUTION e PROFIT sui totali POOLED della finestra (codice puro).

    METODO POOLED (non media di AOV giornalieri):
      avg_AOV          = Σ(revenue) / Σ(ordini)
      avg_COGS/ordine  = Σ(cogs)    / Σ(ordini)

    CONTRIBUTION break-even (esclude i costi fissi):
      contrib/ordine   = avg_AOV − avg_COGS/ordine − fee/ordine(7.5%·AOV) − spedizione($7)
      contribution CPA = contrib/ordine   ·  contribution ROAS = avg_AOV / contrib/ordine

    PROFIT break-even (include la quota costi fissi, dipende dal VOLUME ordini):
      fixed/ordine     = Σ(quota fissa giornaliera DATATA) / Σ(ordini)
                       = quota_fissa_giornaliera / (ordini medi al giorno della finestra)
      profit CPA       = contribution CPA − fixed/ordine
      profit ROAS      = avg_AOV / profit CPA

    Ritorna un dict con roas/cpa (contribution) + profit_roas/profit_cpa +
    avg_orders_per_day + fixed_per_order. Valori None se ordini insufficienti / margine ≤ 0.
    """
    empty = {"roas": None, "cpa": None, "profit_roas": None, "profit_cpa": None,
             "avg_orders_per_day": 0.0, "fixed_per_order": 0.0}
    total_rev = sum(_to_float(r.get("revenue")) for r in prev_days_rows)
    total_orders = sum(int(r.get("num_orders") or 0) for r in prev_days_rows)
    total_cogs = sum(_to_float(r.get("cogs_total")) for r in prev_days_rows)
    if total_orders <= 0:
        return empty

    avg_aov = total_rev / total_orders
    avg_cogs_per_order = total_cogs / total_orders
    be_cpa = (
        avg_aov
        - avg_cogs_per_order
        - settings.FEE_PAGAMENTI * avg_aov
        - settings.SPEDIZIONE_PER_ORDINE
    )
    be_roas = (avg_aov / be_cpa) if be_cpa > 0 else None

    # Quota costi fissi per ordine: Σ quota giornaliera DATATA sui giorni della finestra ÷ ordini.
    from src.metrics.fixed_costs import daily_fixed_allocation

    day_vals = [str(r.get("day")) for r in prev_days_rows if r.get("day")]
    num_days = len(set(day_vals)) or len(prev_days_rows) or 1
    fixed_total = sum(daily_fixed_allocation(d) for d in day_vals)
    fixed_per_order = (fixed_total / total_orders) if total_orders else 0.0
    avg_orders_per_day = total_orders / num_days if num_days else float(total_orders)

    profit_cpa = (be_cpa - fixed_per_order) if be_cpa is not None else None
    profit_roas = (avg_aov / profit_cpa) if (profit_cpa and profit_cpa > 0) else None

    return {"roas": be_roas, "cpa": be_cpa, "profit_roas": profit_roas,
            "profit_cpa": profit_cpa, "avg_orders_per_day": avg_orders_per_day,
            "fixed_per_order": fixed_per_order}


def compute_breakeven(prev_days_rows: list[dict]) -> tuple[Optional[float], Optional[float]]:
    """
    Contribution break-even (ROAS, CPA) sui totali POOLED — retro-compatibile.
    Delega a compute_breakeven_full; ritorna solo la coppia contribution.
    """
    full = compute_breakeven_full(prev_days_rows)
    return full["roas"], full["cpa"]
