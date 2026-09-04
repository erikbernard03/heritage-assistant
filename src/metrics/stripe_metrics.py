"""
Aggregazioni PURE Stripe + refund Shopify (deterministiche, nessuna rete).

Stripe restituisce gli importi nell'unità minima (centesimi per USD): qui si convertono in
dollari. La riconciliazione confronta la revenue Shopify con il lordo processato da Stripe,
il netto (dopo fee reali) e i payout effettivi.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

import pytz

from config import settings


def _cents(v) -> float:
    try:
        return float(v) / 100.0
    except (TypeError, ValueError):
        return 0.0


def _f(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _rome_day(created_unix, tz) -> str:
    return datetime.fromtimestamp(int(created_unix), tz).date().isoformat()


# Tipi di balance transaction che rappresentano VENDITE (entrano nel gross) e RIMBORSI.
# Tutto il resto (payout, transfer, topup, stripe_fee, adjustment, application_fee,
# payout_cancel, payout_failure...) è MOVIMENTO DI CASSA e NON deve entrare nel gross:
# altrimenti il lordo si gonfia (es. contare i payout raddoppierebbe/triplicherebbe i numeri).
_GROSS_TYPES = ("charge", "payment")
_REFUND_TYPES = ("refund", "payment_refund", "refund_failure")


def _usd(amount_cents, currency, exchange_rate) -> float:
    """
    Converte un importo Stripe (nell'unità minima della valuta di SETTLEMENT dell'account) in
    USD. Se la valuta di settlement è USD -> semplice centesimi→dollari. Altrimenti divide per
    l'`exchange_rate` della balance transaction (tasso presentment→settlement): assumendo che il
    cliente paghi in USD (store USD), l'importo in settlement ÷ exchange_rate torna in USD.

    Questo è il fix del bug per cui gli importi in valuta non-USD venivano trattati come cent USD
    e gonfiati di ~6-7× (artefatto di cambio).
    """
    major = _cents(amount_cents)                       # unità maggiori della valuta di settlement
    cur = (currency or "usd").lower()
    if cur == "usd":
        return major
    er = _f(exchange_rate)
    if er > 0:
        return major / er                              # settlement -> USD (presentment)
    return major                                       # nessun tasso: lasciato grezzo (segnalato a monte)


# --------------------------------------------------------------------------- #
# Stripe balance transactions -> daily
# --------------------------------------------------------------------------- #
def daily_from_balance_transactions(
    txns: list[dict], tz_name: Optional[str] = None
) -> dict[str, dict]:
    """
    {giorno Europe/Rome: {gross, fee, net, charge_count, refund_amount, refund_count}} in USD.
    `txns`: balance transactions grezze (created[unix], type, amount, fee — in centesimi della
    valuta di settlement — più currency ed exchange_rate).

    gross = Σ importi dei SOLI tipi charge/payment (convertiti in USD) ; fee = Σ fee di quelle
    transazioni ; refund = Σ |importo refund| ; net = gross − fee − refund. Ogni altro tipo
    (payout/transfer/topup/stripe_fee/adjustment...) è IGNORATO.
    """
    tz = pytz.timezone(tz_name or settings.TIMEZONE)
    out: dict[str, dict] = {}
    for t in txns:
        if t.get("created") is None:
            continue
        day = _rome_day(t["created"], tz)
        acc = out.setdefault(day, {"gross": 0.0, "fee": 0.0, "net": 0.0,
                                   "charge_count": 0, "refund_amount": 0.0, "refund_count": 0})
        typ = str(t.get("type") or "").lower()
        cur = t.get("currency")
        er = t.get("exchange_rate")
        if typ in _GROSS_TYPES:
            acc["gross"] += _usd(t.get("amount"), cur, er)
            acc["fee"] += _usd(t.get("fee"), cur, er)
            acc["charge_count"] += 1
        elif typ in _REFUND_TYPES:
            acc["refund_amount"] += -_usd(t.get("amount"), cur, er)   # importo refund è negativo
            acc["fee"] += _usd(t.get("fee"), cur, er)                 # eventuale fee reversal (di norma 0)
            acc["refund_count"] += 1
        # altri tipi -> movimento di cassa, ignorati
    for acc in out.values():
        acc["net"] = acc["gross"] - acc["fee"] - acc["refund_amount"]
    return out


def settlement_to_usd_rate(txns: list[dict]) -> float:
    """
    Tasso effettivo (valuta di settlement → USD) ricavato dai balance transaction di vendita del
    periodo: Σ(USD) / Σ(importo in valuta di settlement). Serve per convertire i PAYOUT, che non
    espongono un exchange_rate proprio. Ritorna 1.0 se già in USD o dati insufficienti.
    """
    num = 0.0  # USD
    den = 0.0  # settlement (major)
    for t in txns:
        typ = str(t.get("type") or "").lower()
        if typ not in _GROSS_TYPES and typ not in _REFUND_TYPES:
            continue
        major = abs(_cents(t.get("amount")))
        if major == 0:
            continue
        den += major
        num += abs(_usd(t.get("amount"), t.get("currency"), t.get("exchange_rate")))
    return (num / den) if den > 0 else 1.0


def convert_payouts_usd(payouts: list[dict], rate: float) -> list[dict]:
    """
    Converte gli importi dei payout in USD. `amount` in ingresso è in unità MAGGIORI della valuta
    di settlement; `rate` = USD per 1 unità di settlement (da settlement_to_usd_rate). I payout
    già in USD restano invariati.
    """
    out: list[dict] = []
    for p in payouts:
        cur = str(p.get("currency") or "usd").lower()
        amt = _f(p.get("amount"))
        q = dict(p)
        q["amount"] = amt if cur == "usd" else amt * _f(rate)
        out.append(q)
    return out


def stripe_monthly(rows: list[dict]) -> dict[str, dict]:
    """Aggrega stripe_daily per mese: {mese: {gross, fee, net, charge_count, refund_amount, refund_count}}."""
    by: dict[str, dict] = {}
    for r in rows:
        month = str(r.get("day", ""))[:7]
        acc = by.setdefault(month, {"gross": 0.0, "fee": 0.0, "net": 0.0,
                                    "charge_count": 0, "refund_amount": 0.0, "refund_count": 0})
        acc["gross"] += _f(r.get("gross_amount"))
        acc["fee"] += _f(r.get("fee_amount"))
        acc["net"] += _f(r.get("net_amount"))
        acc["charge_count"] += int(_f(r.get("charge_count")))
        acc["refund_amount"] += _f(r.get("refund_amount"))
        acc["refund_count"] += int(_f(r.get("refund_count")))
    return {k: by[k] for k in sorted(by)}


def payouts_monthly(rows: list[dict]) -> dict[str, float]:
    """Somma dei payout (paid) per mese di ARRIVO: {mese: amount}."""
    by: dict[str, float] = {}
    for r in rows:
        arr = str(r.get("arrival_date", ""))[:7]
        if not arr:
            continue
        if str(r.get("status", "paid")).lower() in ("paid", "in_transit", "pending"):
            by[arr] = by.get(arr, 0.0) + _f(r.get("amount"))
    return {k: by[k] for k in sorted(by)}


def fee_rate(gross: float, fee: float) -> Optional[float]:
    """Fee rate reale = fee ÷ gross (frazione). None se gross 0."""
    return (_f(fee) / _f(gross)) if _f(gross) > 0 else None


def total_payment_cost_rate(
    gross: float, fee: float, surcharge_pct: Optional[float] = None
) -> dict:
    """
    Costo di pagamento TOTALE stimato = fee Stripe reale + surcharge Shopify sul gateway
    (fatturata da Shopify, invisibile a Stripe). Confrontarlo con FEE_PAGAMENTI (7.5%): la
    sola fee Stripe SOTTOSTIMA il costo reale quando si usa Stripe tramite Shopify.

    Ritorna {stripe_rate, surcharge_rate, total_rate} come FRAZIONI (es. 0.062).
    `stripe_rate`/`total_rate` = None se gross 0. `surcharge_pct` è una frazione; se None
    usa settings.SHOPIFY_GATEWAY_SURCHARGE_PCT.
    """
    sr = fee_rate(gross, fee)
    surcharge = (
        float(settings.SHOPIFY_GATEWAY_SURCHARGE_PCT)
        if surcharge_pct is None
        else float(surcharge_pct)
    )
    return {
        "stripe_rate": sr,
        "surcharge_rate": surcharge,
        "total_rate": (sr + surcharge) if sr is not None else None,
    }


def dispute_rate(disputes_count: int, charges_count: int) -> Optional[float]:
    """Tasso dispute = dispute ÷ charge (frazione). None se 0 charge."""
    return (int(disputes_count) / int(charges_count)) if int(charges_count) > 0 else None


def reconciliation_row(shopify_revenue: float, stripe_gross: float, stripe_net: float,
                       payouts_amount: float) -> dict:
    """
    Riga di riconciliazione per un mese. `diff_pct` = (stripe_gross − shopify_revenue)/revenue.
    Un lordo Stripe < revenue Shopify è atteso (quota PayPal non passa da Stripe); i payout
    possono differire dal netto per timing (arrivano a giorni di distanza).
    """
    rev = _f(shopify_revenue)
    gross = _f(stripe_gross)
    diff = gross - rev
    return {
        "shopify_revenue": rev,
        "stripe_gross": gross,
        "stripe_net": _f(stripe_net),
        "payouts": _f(payouts_amount),
        "diff": diff,
        "diff_pct": (diff / rev * 100.0) if rev else None,
    }


# --------------------------------------------------------------------------- #
# Refund Shopify (visibilità; Stripe non vede i refund PayPal)
# --------------------------------------------------------------------------- #
def refunds_from_orders(orders: list[dict]) -> dict[str, dict]:
    """
    {giorno Europe/Rome: {amount, count}} dai record refund degli ordini del giorno.
    Importo = Σ transazioni di refund; conteggio = numero di refund. USD.

    NB: usa il giorno dell'ORDINE (report day); i refund su ordini di giorni precedenti non
    sono catturati dal pull giornaliero — è una vista di sola visibilità.
    """
    out: dict[str, dict] = {}
    for o in orders:
        refunds = o.get("refunds") or []
        if not refunds:
            continue
        day = o.get("_day_rome") or str(o.get("created_at", ""))[:10]
        if not day:
            continue
        acc = out.setdefault(day, {"amount": 0.0, "count": 0})
        for rf in refunds:
            amt = 0.0
            for tx in (rf.get("transactions") or []):
                amt += _f(tx.get("amount"))
            if amt == 0.0:
                # fallback: order_adjustments o refund_line_items
                for adj in (rf.get("order_adjustments") or []):
                    amt += abs(_f(adj.get("amount")))
            acc["amount"] += amt
            acc["count"] += 1
    return out


def refunds_monthly(rows: list[dict]) -> dict[str, dict]:
    """Aggrega refunds_daily per mese: {mese: {amount, count}}."""
    by: dict[str, dict] = {}
    for r in rows:
        month = str(r.get("day", ""))[:7]
        acc = by.setdefault(month, {"amount": 0.0, "count": 0})
        acc["amount"] += _f(r.get("refund_amount"))
        acc["count"] += int(_f(r.get("refund_count")))
    return {k: by[k] for k in sorted(by)}
