"""
Connettore Stripe — SOLA LETTURA.

Usa una API key RISTRETTA read-only (rk_live_...) con SOLO questi permessi:
  Charges: Read · Balance transactions: Read · Payouts: Read · Disputes: Read
(niente scrittura, niente altri scope). La chiave vive solo in STRIPE_API_KEY (env).

Ritorna:
- balance_transactions: dict grezzi {created(unix), type, amount(cent), fee(cent)} per il
  bucketing giornaliero (src/metrics/stripe_metrics.daily_from_balance_transactions).
- payouts / disputes: già normalizzati (importi in USD, date ISO) per il salvataggio diretto.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from config import settings


class StripeError(RuntimeError):
    """Errore generico del connettore Stripe."""


def _iso_date(unix) -> Optional[str]:
    if not unix:
        return None
    return datetime.utcfromtimestamp(int(unix)).date().isoformat()


class StripeConnector:
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or settings.STRIPE_API_KEY
        if not self.api_key:
            raise StripeError("STRIPE_API_KEY non configurata (.env).")
        try:
            import stripe
        except ImportError as exc:  # pragma: no cover
            raise StripeError("pacchetto 'stripe' non installato.") from exc
        self._stripe = stripe
        self._stripe.api_key = self.api_key
        self._stripe.max_network_retries = 2

    @staticmethod
    def _unix(d: date) -> int:
        return int(datetime(d.year, d.month, d.day).timestamp())

    def balance_transactions(self, start: date, end: date) -> list[dict]:
        """
        Balance transactions con created in [start, end] (end incluso). Grezze: created, type,
        amount(cent), fee(cent). Include charge/payment/refund e altri tipi (filtrati a valle).
        """
        gte = self._unix(start)
        lt = self._unix(end) + 86400          # end incluso -> < giorno dopo
        out: list[dict] = []
        it = self._stripe.BalanceTransaction.list(
            created={"gte": gte, "lt": lt}, limit=100
        )
        for bt in it.auto_paging_iter():
            out.append({"created": bt.get("created"), "type": bt.get("type"),
                        "amount": bt.get("amount"), "fee": bt.get("fee")})
        return out

    def payouts(self, start: date, end: date) -> list[dict]:
        """Payout con arrival_date in [start, end]. Normalizzati (USD, date ISO)."""
        gte = self._unix(start)
        lt = self._unix(end) + 86400
        out: list[dict] = []
        it = self._stripe.Payout.list(arrival_date={"gte": gte, "lt": lt}, limit=100)
        for p in it.auto_paging_iter():
            out.append({
                "id": p.get("id"),
                "arrival_date": _iso_date(p.get("arrival_date")),
                "amount": (float(p.get("amount") or 0) / 100.0),
                "status": p.get("status"),
                "created": _iso_date(p.get("created")),
            })
        return out

    def disputes(self, start: date, end: date) -> list[dict]:
        """Dispute con created in [start, end]. Normalizzate (USD, date/deadline ISO)."""
        gte = self._unix(start)
        lt = self._unix(end) + 86400
        out: list[dict] = []
        it = self._stripe.Dispute.list(created={"gte": gte, "lt": lt}, limit=100)
        for d in it.auto_paging_iter():
            ev = d.get("evidence_details") or {}
            out.append({
                "id": d.get("id"),
                "amount": (float(d.get("amount") or 0) / 100.0),
                "status": d.get("status"),
                "reason": d.get("reason"),
                "created": _iso_date(d.get("created")),
                "evidence_due": _iso_date(ev.get("due_by")),
            })
        return out

    def account_id(self) -> Optional[str]:
        """ID account (diagnostica). Best-effort."""
        try:
            acct = self._stripe.Account.retrieve()
            return acct.get("id")
        except Exception:  # noqa: BLE001
            return None
