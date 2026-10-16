"""
Cache Klaviyo a finestra piena — DUE LIVELLI: L1 in-process + L2 Supabase (persistente).

Il reporting Klaviyo ha un rate limit bassissimo e la cache in-process si perde ad ogni
restart/redeploy Railway: il primo load della dashboard ri-tirava TUTTI i mesi in sequenza.
Con la cache persistente su Supabase (klaviyo_monthly_cache) il cold start legge dal DB.

Regole:
- mesi PASSATI (chiusi, immutabili): se in cache (L1 o L2) -> serviti per sempre, MAI fetch;
  se assenti -> fetch una volta sola, scritti in DB.
- mese CORRENTE: cache se più fresca di 6h; altrimenti refetch + aggiorna L1/L2
  (stale-while-revalidate: se il refetch fallisce si serve lo stale).

Modulo PURO rispetto a Streamlit (nessun import pandas/streamlit): testabile a sé.
"""
from __future__ import annotations

import time as _time
from typing import Optional

_KLA_STORE: dict = {}          # L1: (start,end) -> {"data": kp, "ts": epoch}
_KLA_TTL = 6 * 3600            # mese corrente: freschezza 6h


def _default_fetch(start: str, end: str) -> dict:
    from src.report import load_klaviyo_period

    kp = load_klaviyo_period(start, end)
    if not kp.get("ok") or kp.get("campaigns_revenue") is None:
        raise RuntimeError(kp.get("error") or "Klaviyo period query failed")
    return kp


def _db_get(month: str) -> Optional[dict]:
    """L2: legge la cache persistente Supabase. {"data": kp, "ts": epoch} | None. Best-effort."""
    try:
        from src.db.supabase_client import SupabaseStore

        hit = SupabaseStore().get_klaviyo_month_cache(month)
        return {"data": hit["kp"], "ts": hit["ts"]} if hit else None
    except Exception as exc:  # noqa: BLE001 — la dashboard non deve rompersi per la cache DB
        print(f"[klaviyo_cache] DB read failed for {month}: {exc}")
        return None


def _db_put(month: str, kp: dict) -> None:
    """L2: scrive la cache persistente Supabase (best-effort)."""
    try:
        from src.db.supabase_client import SupabaseStore

        SupabaseStore().upsert_klaviyo_month_cache(month, kp)
    except Exception as exc:  # noqa: BLE001
        print(f"[klaviyo_cache] DB write failed for {month}: {exc}")


def get_klaviyo(start: str, end: str, is_current: bool) -> tuple:
    """
    Ritorna (kp | None, stale: bool, error: str | None), con cache L1 (process) + L2 (Supabase).
    - PASSATO in cache (L1/L2) -> servito, MAI fetch (mesi immutabili).
    - CORRENTE fresco (<6h) -> servito; stale -> refetch e aggiorna L1+L2; se il refetch
      fallisce -> si serve lo stale (stale=True).
    - nessuna cache -> fetch, scrive L1+L2; se fallisce (None, False, error) -> il chiamante
      usa il fallback DB "(snapshot)".
    """
    key = (start, end)
    month = start[:7]
    now = _time.time()

    cached = _KLA_STORE.get(key)
    if cached is None:
        db = _db_get(month)
        if db is not None:
            cached = db
            _KLA_STORE[key] = db                 # promuovi a L1

    if cached is not None and (not is_current or (now - cached["ts"] < _KLA_TTL)):
        return cached["data"], False, None

    try:
        kp = _default_fetch(start, end)
        _db_put(month, kp)
        _KLA_STORE[key] = {"data": kp, "ts": now}
        return kp, False, None
    except Exception as exc:  # noqa: BLE001
        if cached is not None:                   # stale-while-revalidate
            return cached["data"], True, str(exc)
        return None, False, str(exc)
