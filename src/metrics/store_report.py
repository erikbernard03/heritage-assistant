"""
Viste PER STORE per i report (Telegram) e la dashboard — separazione completa per store.

Ogni store ha le SUE metriche complete (revenue, ordini, AOV, COGS, spedizione, fee con la
propria aliquota, ad spend, net operating) e il SUO break-even CONTRIBUTION (dal proprio
AOV/COGS/fee). I costi fissi restano UN solo pot a livello TOTALE: il profit break-even e il
net profit "netto" si calcolano solo sul totale, non per store. Codice puro/deterministico.
"""
from __future__ import annotations

from typing import Optional

from config import settings
from src.metrics.profit import compute_breakeven_full

# label store -> (emoji, nome visualizzato, aliquota fee)
STORE_META = {
    "com": ("🏪", "heritagering.com", settings.PAYMENT_FEE_RATE_COM),
    "co": ("🌎", "heritagering.co", settings.PAYMENT_FEE_RATE_CO),
}


def _f(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def store_fee_rate(label: str) -> float:
    return STORE_META.get(label, ("", "", settings.FEE_PAGAMENTI))[2]


def build_store_view(label: str, revenue: float, orders: int, cogs_total: float,
                     ads_spend: float, breakeven: dict,
                     shipping_total: Optional[float] = None,
                     payment_fees: Optional[float] = None,
                     store_sessions: Optional[int] = None) -> dict:
    """
    Vista di uno store. shipping/fees ricavati deterministicamente se non passati:
    shipping = $7 × ordini ; fees = aliquota_store × revenue. net_operating = revenue − COGS −
    shipping − fees − ads. AOV e COGS/ordine dai propri totali. store_cvr = ordini ÷ sessioni
    dello store (None se sessioni non disponibili).
    """
    revenue = _f(revenue)
    orders = int(orders or 0)
    cogs_total = _f(cogs_total)
    ads_spend = _f(ads_spend)
    rate = store_fee_rate(label)
    ship = _f(shipping_total) if shipping_total is not None else settings.SPEDIZIONE_PER_ORDINE * orders
    fees = _f(payment_fees) if payment_fees is not None else rate * revenue
    net_op = revenue - cogs_total - ship - fees - ads_spend
    sessions = int(store_sessions) if store_sessions not in (None, "") else None
    return {
        "label": label,
        "revenue": revenue,
        "orders": orders,
        "aov": (revenue / orders) if orders else 0.0,
        "cogs_total": cogs_total,
        "cogs_per_order": (cogs_total / orders) if orders else 0.0,
        "shipping_total": ship,
        "payment_fees": fees,
        "fee_rate": rate,
        "ads_spend": ads_spend,
        "net_operating": net_op,
        "store_sessions": sessions,
        "store_cvr": (orders / sessions) if sessions else None,
        "breakeven": breakeven or {},
    }


def store_breakeven(rows: list[dict], label: str) -> dict:
    """Break-even CONTRIBUTION di uno store dai suoi rows pooled, con la SUA aliquota fee."""
    return compute_breakeven_full(rows, fee_rate=store_fee_rate(label))


# --------------------------------------------------------------------------- #
# Rendering Telegram (Markdown v1)
# --------------------------------------------------------------------------- #
def format_store_section(view: dict, meta_daily: Optional[dict] = None,
                         google_daily: Optional[dict] = None) -> str:
    """
    Sezione compatta di UNO store. Meta/Google ROAS vs break-even mostrati solo se passati
    (per convenzione oggi solo per il .com). Se lo store ha 0 ordini -> riga "no orders".
    """
    label = view["label"]
    icon, name, _rate = STORE_META.get(label, ("🏪", label, settings.FEE_PAGAMENTI))
    if view["orders"] <= 0:
        return f"\n{icon} *{name}* — no orders"

    be = view.get("breakeven") or {}
    be_roas = f"{be['roas']:,.2f}x" if be.get("roas") else "n/a"
    be_cpa = f"${be['cpa']:,.2f}" if be.get("cpa") is not None else "n/a"
    lines = [
        f"\n{icon} *{name}* _(USD)_",
        f"   💰 Revenue *${view['revenue']:,.2f}* · 🛒 Orders *{view['orders']}* · "
        f"🧾 AOV ${view['aov']:,.2f}",
        f"   🏷️ COGS ${view['cogs_total']:,.2f} (${view['cogs_per_order']:,.2f}/order) · "
        f"📦 Shipping ${view['shipping_total']:,.2f} · "
        f"💳 Fees ${view['payment_fees']:,.2f} ({view['fee_rate']*100:.1f}%)",
    ]
    if view["ads_spend"] > 0:
        lines.append(f"   📣 Ad spend ${view['ads_spend']:,.2f}")
    # CVR proprio dello store (ordini ÷ sessioni dello store). Net operating/profit sono SOLO
    # sulla riga Σ TOTAL.
    cvr = view.get("store_cvr")
    cvr_s = f"{cvr*100:.2f}%" if cvr else "n/a"
    lines.append(f"   📈 CVR {cvr_s}")
    lines.append(f"   ⚖️ Break-even ROAS {be_roas} · CPA {be_cpa} _(own AOV/COGS)_")
    # Meta/Google ROAS confrontati col break-even DELLO STORE (solo dove passati).
    ref = be.get("roas")
    for emoji, nm, d in (("📣", "Meta", meta_daily), ("🔎", "Google", google_daily)):
        if d and float(d.get("roas") or 0) > 0:
            r = float(d["roas"])
            vs = f" (break-even {ref:,.2f}x)" if ref else ""
            lines.append(f"   {emoji} {nm} ROAS {r:,.2f}x{vs}")
    return "\n".join(lines)


def format_total_line(revenue: float, orders: int, net_operating: float,
                      fixed_cost: float) -> str:
    """
    Riga TOTAL: revenue, ordini, net operating e net profit (netto = operating − quota fissa).
    I costi fissi sono UN solo pot, applicato SOLO qui a livello totale.
    """
    net = _f(net_operating) - _f(fixed_cost)
    return (
        f"\nΣ *TOTAL* — Revenue *${_f(revenue):,.2f}* · Orders *{int(orders or 0)}* · "
        f"Net operating *${_f(net_operating):,.2f}* · "
        f"Net _(− fixed ${_f(fixed_cost):,.2f})_ *${net:,.2f}*"
    )
