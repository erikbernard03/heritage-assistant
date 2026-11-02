"""
Calcoli deterministici del net profit (codice puro — MAI AI sui numeri).

Formula net profit giornaliero (Fase 1, solo Shopify => spesa ads = 0):

    net_profit =
        revenue_reale_shopify
      − COGS_totale            (somma per line item via handle; $0/sconosciuto = $3)
      − costi_spedizione       (datati: flat $7×ordini prima del 2026-11-01; dal 2026-11-01
                                formula per-ordine country_base+piece_surcharge, somma per-ordine)
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


def _shop_money(order: dict, *keys) -> Optional[float]:
    """Primo `<key>_set.shop_money.amount` presente (valuta base dello store)."""
    for k in keys:
        s = order.get(k)
        if isinstance(s, dict):
            amt = (s.get("shop_money") or {}).get("amount")
            if amt not in (None, ""):
                return _to_float(amt)
    return None


def _order_current_tax(order: dict) -> float:
    """
    Tasse ATTUALI dell'ordine nella VALUTA BASE dello store (`current_total_tax`), nette dei
    rimborsi esattamente come `current_total_price`. Preferisce `current_total_tax_set.shop_money`;
    fallback al `current_total_tax` top-level. Campo assente/null => 0.

    NB: usiamo SEMPRE `current_total_tax` (MAI il `total_tax` lordo): così la tassa rimborsata
    viene nettata allo stesso modo in cui `current_total_price` netta i rimborsi dell'ordine.
    """
    tax = _shop_money(order, "current_total_tax_set")
    if tax is None:
        t = order.get("current_total_tax")
        tax = _to_float(t) if t not in (None, "") else 0.0
    return tax


def order_revenue_usd(order: dict, currency_to_usd: float = 1.0) -> float:
    """
    Revenue dell'ordine in USD, multi-valuta e **AL NETTO DELLE TASSE**:

        revenue = current_total_price − current_total_tax        (valuta base dello store)
                × currency_to_usd

      - prezzo: `current_total_price_set.shop_money` (importo nella VALUTA BASE dello store, già
        convertito da Shopify dal presentment); fallback total_price_set.shop_money, poi
        current_total_price/total_price (top-level);
      - tasse: `current_total_tax` (shop_money, netto dei rimborsi), assente/null => 0;
      - il tutto × `currency_to_usd` (così la logica multi-valuta del .co continua a funzionare).

    Le tasse NON sono mai nostre (sono dovute allo Stato): non entrano MAI in revenue né in
    profit. Per lo store .com (valuta base USD, currency_to_usd=1.0) revenue = prezzo − tasse.
    """
    shop = _shop_money(order, "current_total_price_set", "total_price_set")
    price = shop if shop is not None else order_revenue(order)
    tax = _order_current_tax(order)
    return (price - tax) * float(currency_to_usd or 1.0)


def order_tax_usd(order: dict, currency_to_usd: float = 1.0) -> float:
    """
    Tasse incassate sull'ordine in USD (`current_total_tax`, netto dei rimborsi) × fx.
    Serve per la riga 'Tax collected' della riconciliazione Stripe e per store_daily.tax_total.
    Non è revenue: è solo visibilità (le tasse sono dovute allo Stato).
    """
    return _order_current_tax(order) * float(currency_to_usd or 1.0)


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
    """IVA/tasse LORDE incassate dal cliente (`total_tax`, pre-rimborso). Usato SOLO dall'audit
    diagnostico per la verifica dell'identità lorda subtotal+tax+shipping = total_price.
    Per le metriche (revenue/visibilità) si usa `order_tax_usd` (current_total_tax, netto)."""
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
    # Spedizione: income già INCLUSO in `revenue`, separato per visibilità (no double count).
    # Tasse: NON più in `revenue` (escluse); `tax_collected` resta solo per visibilità/riconcilio.
    shipping_collected: float = 0.0     # spedizione premium pagata dal cliente (dentro revenue)
    tax_collected: float = 0.0          # IVA/tasse incassate (ESCLUSE da revenue; solo visibilità)
    store_cvr: float = 0.0              # CVR negozio (Shopify primario, TW fallback); frazione
    store_sessions: Optional[int] = None  # sessioni/visitatori reali Shopify (None se non disp.)
    line_items: list[LineItemCost] = field(default_factory=list)

    @property
    def product_revenue(self) -> float:
        """Revenue di solo prodotto = revenue (già netto tasse) − spedizione incassata.
        Le tasse sono GIÀ escluse da `revenue`, quindi NON si sottraggono di nuovo."""
        return self.revenue - self.shipping_collected

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
    fee_rate: Optional[float] = None,
    currency_to_usd: float = 1.0,
) -> DailyMetrics:
    """
    Calcola le metriche del giorno a partire dagli ordini Shopify.

    - `orders`: ordini Shopify (con line_items) creati nel giorno.
    - `handle_map`: product_id -> handle (per risolvere il COGS per handle).
    - `ads_spend`: spesa pubblicitaria totale in USD (Fase 1 = 0).
    - `fee_rate`: aliquota fee pagamenti dello store (default = settings.FEE_PAGAMENTI, .com).
    - `currency_to_usd`: tasso valuta-base-store -> USD (1.0 per il .com in USD).
    Gli ordini cancellati sono esclusi da revenue/conteggio. Revenue convertita in USD.
    """
    resolver = resolver or get_resolver()
    fee_rate = settings.FEE_PAGAMENTI if fee_rate is None else float(fee_rate)
    m = DailyMetrics(day=day)

    for order in orders:
        if _is_cancelled(order):
            continue

        order_id = int(order.get("id", 0))
        # Revenue = (current_total_price − current_total_tax) in USD (netto rimborsi propri e
        # AL NETTO DELLE TASSE, datato sull'ordine). Le tasse non sono mai nostre.
        revenue = order_revenue_usd(order, currency_to_usd)
        m.revenue += revenue
        # spedizione è GIÀ dentro revenue (la separiamo solo per mostrarla); le tasse NON sono
        # in revenue (escluse) ma le tracciamo per la riconciliazione Stripe e store_daily.
        m.shipping_collected += _order_shipping_collected(order)
        m.tax_collected += order_tax_usd(order, currency_to_usd)
        m.num_orders += 1
        # Spedizione DATATA e per-ordine: prima del 2026-11-01 flat $7; dal 2026-11-01 formula
        # country_base + piece_surcharge (resolver, su data Rome del giorno). Cancellati esclusi
        # (siamo dentro il filtro cancelled). currency-agnostica (tariffe già in USD).
        m.shipping_total += resolver.shipping_for_order(order, day)

        for li in order.get("line_items", []):
            product_id = li.get("product_id")
            handle = handle_map.get(int(product_id)) if product_id else None
            title = li.get("title") or ""
            qty = int(li.get("quantity", 1) or 1)
            unit_cogs = resolver.cogs_for_handle(
                handle, title, order_date=day, variant_title=li.get("variant_title"),
            )
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

    # m.shipping_total è già sommato per-ordine nel loop (formula datata). NB: i giorni
    # pre-2026-11-01 danno flat $7/ordine, quindi == settings.SPEDIZIONE_PER_ORDINE × ordini.
    m.payment_fees = fee_rate * m.revenue          # aliquota per-store
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


def combine_daily_metrics(day: str, parts: list["DailyMetrics"]) -> DailyMetrics:
    """
    Somma le metriche PER-STORE in un'unica riga COMBINATA del giorno (Option A: totali combinati).
    - revenue/COGS/shipping/fee/ads/ordini/line_items: sommati (le fee sono già per-store);
    - quota costi fissi: UNA sola volta (pot condiviso, DATATA) — NON sommata dalle parti;
    - net profit ricalcolato dai componenti combinati;
    - sessioni: somma di quelle disponibili (None se nessuno store le espone);
    - store_cvr: lasciato al chiamante (dipende dalle sessioni combinate).
    """
    m = DailyMetrics(day=day)
    for p in parts:
        m.revenue += p.revenue
        m.cogs_total += p.cogs_total
        m.shipping_total += p.shipping_total
        m.payment_fees += p.payment_fees
        m.ads_spend += p.ads_spend
        m.num_orders += p.num_orders
        m.shipping_collected += p.shipping_collected
        m.tax_collected += p.tax_collected
        m.line_items.extend(p.line_items)
    sess = [p.store_sessions for p in parts if p.store_sessions is not None]
    m.store_sessions = sum(sess) if sess else None
    if settings.INCLUDI_COSTI_FISSI_IN_NET_PROFIT:
        from src.metrics.fixed_costs import daily_fixed_allocation

        m.fixed_cost_daily = daily_fixed_allocation(day)   # pot condiviso, una volta
    m.net_profit_operativo = (
        m.revenue - m.cogs_total - m.shipping_total - m.payment_fees - m.ads_spend
    )
    m.net_profit_netto = m.net_profit_operativo - m.fixed_cost_daily
    m.aov = (m.revenue / m.num_orders) if m.num_orders else 0.0
    return m


def compute_breakeven_full(prev_days_rows: list[dict], fee_rate: Optional[float] = None) -> dict:
    """
    Break-even CONTRIBUTION e PROFIT sui totali POOLED della finestra (codice puro).

    `fee_rate`: aliquota fee pagamenti dello store (default settings.FEE_PAGAMENTI = .com 7.5%).
    Per il break-even PER STORE si passa l'aliquota dello store (.com 7.5%, .co 5%).

    METODO POOLED (non media di AOV giornalieri):
      avg_AOV          = Σ(revenue) / Σ(ordini)
      avg_COGS/ordine  = Σ(cogs)    / Σ(ordini)

    CONTRIBUTION break-even (esclude i costi fissi):
      contrib/ordine   = avg_AOV − avg_COGS/ordine − fee/ordine(fee_rate·AOV) − spedizione/ordine
                         (spedizione/ordine = Σ(shipping_total)/Σ(ordini) POOLED dalla finestra)
      contribution CPA = contrib/ordine   ·  contribution ROAS = avg_AOV / contrib/ordine

    PROFIT break-even (include la quota costi fissi, dipende dal VOLUME ordini):
      fixed/ordine     = Σ(quota fissa giornaliera DATATA) / Σ(ordini)
                       = quota_fissa_giornaliera / (ordini medi al giorno della finestra)
      profit CPA       = contribution CPA − fixed/ordine
      profit ROAS      = avg_AOV / profit CPA

    NB: il PROFIT break-even usa i costi fissi (pot condiviso a livello TOTALE) e va calcolato
    solo sul totale; per i singoli store si usa la sola parte CONTRIBUTION.

    Ritorna un dict con roas/cpa (contribution) + profit_roas/profit_cpa +
    avg_orders_per_day + fixed_per_order. Valori None se ordini insufficienti / margine ≤ 0.
    """
    fee_rate = settings.FEE_PAGAMENTI if fee_rate is None else float(fee_rate)
    empty = {"roas": None, "cpa": None, "profit_roas": None, "profit_cpa": None,
             "avg_orders_per_day": 0.0, "fixed_per_order": 0.0}
    total_rev = sum(_to_float(r.get("revenue")) for r in prev_days_rows)
    total_orders = sum(int(r.get("num_orders") or 0) for r in prev_days_rows)
    total_cogs = sum(_to_float(r.get("cogs_total")) for r in prev_days_rows)
    if total_orders <= 0:
        return empty

    # Spedizione/ordine POOLED dalla stessa finestra (come COGS/ordine): Σ(shipping)/Σ(ordini).
    # Usa i valori REALI salvati per-giorno (shipping_total), quindi gestisce da sé la transizione
    # al 2026-11-01 (finestre a cavallo pescano i veri valori per-giorno). Se nessuna riga porta
    # 'shipping_total' (chiamanti legacy/minimali), fallback al flat storico $7/ordine.
    avg_aov = total_rev / total_orders
    avg_cogs_per_order = total_cogs / total_orders
    if any("shipping_total" in r for r in prev_days_rows):
        total_shipping = sum(_to_float(r.get("shipping_total")) for r in prev_days_rows)
        shipping_per_order = total_shipping / total_orders
    else:
        shipping_per_order = float(settings.SPEDIZIONE_PER_ORDINE)
    be_cpa = (
        avg_aov
        - avg_cogs_per_order
        - fee_rate * avg_aov
        - shipping_per_order
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
