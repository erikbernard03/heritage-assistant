"""
Heritage Ring — Dashboard web (Streamlit), SOLA LETTURA sui dati Supabase.

Terzo servizio Railway (accanto a bot e cron). NON scrive mai sul DB: legge le
tabelle già popolate dal job notturno (daily_metrics, meta_*, tiktok_*, google_*,
klaviyo_*) e mostra le stesse metriche dei report Telegram per il PERIODO scelto.

I numeri combaciano SEMPRE con /report7 perché la dashboard riusa la stessa
aggregazione (src.report.aggregate_period -> aggregate_week): totali di periodo,
Store CVR totals-based (conversioni/sessioni, mai media dei tassi giornalieri),
break-even a 4 giorni, quota costi fissi × numero di giorni nel periodo.

Avvio (Railway start command):
    streamlit run src/dashboard/app.py --server.port $PORT --server.address 0.0.0.0

Sicurezza: accesso protetto da DASHBOARD_PASSWORD (env var). Se non impostata,
la dashboard resta bloccata.
"""
from __future__ import annotations

import hmac
import os
import sys
from datetime import date, timedelta

# Streamlit lancia lo script direttamente (streamlit run src/dashboard/app.py), quindi
# sys.path[0] è la cartella dello script (src/dashboard/), NON la root del repo: senza
# questo, gli import di progetto (config, src.*) falliscono con ModuleNotFoundError.
# Aggiungiamo la ROOT del repo (…/heritage-assistant) a sys.path PRIMA di ogni import di
# progetto. Indipendente dalla working directory (usa __file__, non os.getcwd()).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from config import settings  # noqa: E402
from src.dashboard import periods  # noqa: E402

st.set_page_config(
    page_title="Heritage Ring · Dashboard",
    page_icon="📊",
    layout="centered",              # 'centered' = leggibile su iPhone
    initial_sidebar_state="collapsed",
)

# CSS mobile-first: metriche grandi, meno clutter, tabelle scrollabili.
st.markdown(
    """
    <style>
      .block-container {padding-top: 1.2rem; padding-bottom: 3rem; max-width: 760px;}
      [data-testid="stMetricValue"] {font-size: 1.9rem; font-weight: 700;}
      [data-testid="stMetricLabel"] {font-size: 0.8rem; opacity: 0.75;}
      h1 {font-size: 1.5rem !important;}
      h2 {font-size: 1.15rem !important; margin-top: 1.4rem;}
      .stDataFrame {font-size: 0.85rem;}
      footer {visibility: hidden;}
    </style>
    """,
    unsafe_allow_html=True,
)


# --------------------------------------------------------------------------- #
# Autenticazione (password via env DASHBOARD_PASSWORD)
# --------------------------------------------------------------------------- #
def _check_password() -> bool:
    """Ritorna True se l'utente ha inserito la password corretta (gate su tutto)."""
    expected = settings.DASHBOARD_PASSWORD or ""
    if not expected:
        st.error("🔒 Dashboard non configurata: manca DASHBOARD_PASSWORD.")
        st.stop()

    if st.session_state.get("auth_ok"):
        return True

    st.title("🔒 Heritage Ring")
    pw = st.text_input("Password", type="password")
    if pw:
        if hmac.compare_digest(pw, expected):
            st.session_state["auth_ok"] = True
            return True
        st.error("Password errata.")
    return False


# --------------------------------------------------------------------------- #
# Caricamento + aggregazione (cache breve: i dati cambiano solo di notte)
# --------------------------------------------------------------------------- #
@st.cache_data(ttl=900, show_spinner=False)
def _compute_period(start_iso: str, end_iso: str) -> dict | None:
    """
    Carica daily_metrics nel range e aggrega ESATTAMENTE come /report7.
    Ritorna un dict serializzabile (per la cache) o None se non ci sono dati.
    """
    from src.db.supabase_client import SupabaseStore
    from src.report import aggregate_period

    store = SupabaseStore()
    daily_rows = store.get_daily_metrics_range(start_iso, end_iso)
    if not daily_rows:
        return None

    (m, meta_daily, meta_campaigns, tiktok_daily, google_daily,
     klaviyo_daily, klaviyo_campaigns, breakeven, _header) = aggregate_period(
        daily_rows, store,
    )
    be_roas, be_cpa = breakeven or (None, None)
    days = sorted({r["day"] for r in daily_rows})

    # Serie giornaliera break-even/AOV: serve un lookback PRIMA dell'inizio periodo, così
    # anche i primi giorni hanno i loro 4 giorni precedenti (stessa formula del report).
    from src.dashboard.monthly import daily_breakeven_series

    lb_start = (date.fromisoformat(start_iso) - timedelta(days=20)).isoformat()
    lookback_rows = store.get_daily_metrics_range(lb_start, end_iso)
    be_series = daily_breakeven_series(lookback_rows, set(days))

    # Sorgenti last-click + pixel Triple Whale del periodo (best-effort: tabelle Fase 9).
    source_agg: dict[str, dict] = {}
    tw_pixel_agg: dict[str, dict] = {}
    try:
        for r in store.get_table_range("orders_by_source_daily", start_iso, end_iso):
            acc = source_agg.setdefault(r.get("source") or "other", {"orders": 0, "revenue": 0.0})
            acc["orders"] += int(r.get("orders") or 0)
            acc["revenue"] += float(r.get("revenue") or 0)
    except Exception:  # noqa: BLE001 — migration 014 non ancora eseguita
        source_agg = {}
    try:
        for r in store.get_table_range("tw_pixel_daily", start_iso, end_iso):
            acc = tw_pixel_agg.setdefault(
                r.get("channel") or "other",
                {"orders": 0.0, "revenue": 0.0, "kind": r.get("kind") or "platform-reported"})
            acc["orders"] += float(r.get("orders") or 0)
            acc["revenue"] += float(r.get("revenue") or 0)
            if r.get("kind"):
                acc["kind"] = r.get("kind")
    except Exception:  # noqa: BLE001
        tw_pixel_agg = {}

    return {
        "metrics": {
            "num_orders": m.num_orders, "revenue": m.revenue, "aov": m.aov,
            "cogs_total": m.cogs_total, "shipping_total": m.shipping_total,
            "payment_fees": m.payment_fees, "ads_spend": m.ads_spend,
            "fixed_cost_daily": m.fixed_cost_daily,
            "net_profit_operativo": m.net_profit_operativo,
            "net_profit_netto": m.net_profit_netto, "store_cvr": m.store_cvr,
        },
        "meta_daily": meta_daily, "tiktok_daily": tiktok_daily,
        "google_daily": google_daily, "klaviyo_daily": klaviyo_daily,
        "meta_campaigns": meta_campaigns,
        "breakeven": {"roas": be_roas, "cpa": be_cpa},
        "num_days": len(days),
        "daily_rows": sorted(daily_rows, key=lambda r: r["day"]),
        "be_series": be_series,
        "source_agg": source_agg,
        "tw_pixel_agg": tw_pixel_agg,
        "meta_self": {"orders": int((meta_daily or {}).get("orders") or 0),
                      "revenue": float((meta_daily or {}).get("revenue") or 0.0)},
        "google_self": {"orders": int((google_daily or {}).get("orders") or 0),
                        "revenue": float((google_daily or {}).get("revenue") or 0.0)},
    }


@st.cache_data(ttl=300, show_spinner="Pulling today live…")
def _compute_today_live() -> dict | None:
    """
    OGGI in tempo reale: stesso percorso LIVE di /today (report._gather_day) — ordini Shopify
    finora, Meta bucketizzato orario→Roma (solo ore di oggi), Triple Whale (todayHour), Klaviyo
    giornaliero. NON persiste. Break-even dai numeri di OGGI (own day). Cache 5 minuti per non
    martellare le API. Attribuzione ads provvisoria (si assesta nelle ore successive).
    """
    from src.report import _gather_day, _own_day_breakeven, day_window

    today = periods.rome_today()
    g = _gather_day(day_window(today), persist=False)
    m = g.metrics
    be_roas, be_cpa = _own_day_breakeven(m)
    return {
        "metrics": {
            "num_orders": m.num_orders, "revenue": m.revenue, "aov": m.aov,
            "cogs_total": m.cogs_total, "shipping_total": m.shipping_total,
            "payment_fees": m.payment_fees, "ads_spend": m.ads_spend,
            "fixed_cost_daily": m.fixed_cost_daily,
            "net_profit_operativo": m.net_profit_operativo,
            "net_profit_netto": m.net_profit_netto, "store_cvr": m.store_cvr,
        },
        "meta_daily": g.meta_daily, "tiktok_daily": g.tiktok_daily,
        "google_daily": g.google_daily, "klaviyo_daily": g.klaviyo_daily,
        "meta_campaigns": g.meta_campaigns,
        "breakeven": {"roas": be_roas, "cpa": be_cpa},
        "num_days": 1,
    }


@st.cache_data(ttl=900, show_spinner=False)
def _compute_monthly() -> dict | None:
    """
    Vista MENSILE: un record per mese di calendario con dati. Riusa aggregate_period
    (stessi totali di /report7) e le unità prodotto da product_units_daily.
    """
    import calendar

    from src.db.supabase_client import SupabaseStore
    from src.dashboard.monthly import (
        filter_visible_months,
        group_by_month,
        monthly_store_cvr,
        monthly_visitors,
        units_by_month,
        weekday_net_profit,
    )
    from src.report import aggregate_period

    store = SupabaseStore()
    today = periods.rome_today()
    today_iso = today.isoformat()
    current_month = f"{today.year}-{today.month:02d}"
    all_rows = store.get_daily_metrics_range("2000-01-01", today_iso)
    if not all_rows:
        return None

    # NB: questo compute NON fa chiamate live a Klaviyo (l'endpoint reporting ha un rate
    # limit bassissimo). I valori Klaviyo del DB (snapshot notturni) sono la BASELINE; le
    # viste live a finestra piena si caricano a parte, con cache persistente + stale-while-
    # revalidate (vedi _get_klaviyo e _render_monthly_tab).
    months: list[dict] = []
    for month, rows, partial in filter_visible_months(group_by_month(all_rows), today):
        (m, meta_daily, _mc, tiktok_daily, google_daily,
         db_klaviyo, _kc, _be, _h) = aggregate_period(rows, store)
        visitors, est = monthly_visitors(rows)

        y, mo = (int(x) for x in month.split("-"))
        m_start = f"{month}-01"
        last_dom = calendar.monthrange(y, mo)[1]
        m_end = min(f"{month}-{last_dom:02d}", today_iso)   # mese corrente -> fino a oggi

        months.append({
            "month": month, "partial": partial,
            "is_current": month == current_month,
            "klaviyo_start": m_start, "klaviyo_end": m_end,
            "day_strs": sorted(r2["day"] for r2 in rows),   # per la quota costi fissi datata
            "weekday_profit": weekday_net_profit(rows),      # net profit medio per weekday
            "revenue": m.revenue, "orders": m.num_orders, "aov": m.aov,
            # CVR mensile = ordini ÷ sessioni reali (None -> "n/a", non 0.00%)
            "store_cvr": monthly_store_cvr(rows),
            "visitors": visitors, "visitors_est": est,
            "meta_roas": float((meta_daily or {}).get("roas") or 0.0),
            "google_roas": float((google_daily or {}).get("roas") or 0.0),
            "tiktok_roas": float((tiktok_daily or {}).get("roas") or 0.0),
            # Auto-attribuzione piattaforma (per il confronto a 3 vie con pixel TW e last-click)
            "meta_self": {"orders": int((meta_daily or {}).get("orders") or 0),
                          "revenue": float((meta_daily or {}).get("revenue") or 0.0)},
            "google_self": {"orders": int((google_daily or {}).get("orders") or 0),
                            "revenue": float((google_daily or {}).get("revenue") or 0.0)},
            "total_ad_spend": float(m.ads_spend or 0.0),   # Meta + TikTok + Google
            "cogs_total": float(m.cogs_total or 0.0),
            "shipping_total": float(m.shipping_total or 0.0),
            "payment_fees": float(m.payment_fees or 0.0),
            # BASELINE dal DB (snapshot notturni); il TABLE usa i valori live (vedi tab).
            "klaviyo_campaigns_revenue": float((db_klaviyo or {}).get("revenue") or 0.0),
            "klaviyo_flows_revenue": (db_klaviyo or {}).get("flow_revenue"),
            "net_profit_operativo": m.net_profit_operativo,
            "net_profit_netto": m.net_profit_netto,
        })

    from src.dashboard.monthly import (
        google_by_month,
        meta_campaigns_by_month,
    )
    from src.metrics.sales_location import sales_by_country_by_month
    from src.metrics.sales_timing import sales_by_hour_by_month
    from src.metrics.stripe_metrics import (
        payouts_monthly,
        refunds_monthly,
        stripe_monthly,
    )

    units_rows = store.get_table_range("product_units_daily", "2000-01-01", today_iso)
    meta_camp_rows = store.get_table_range("meta_campaigns", "2000-01-01", today_iso)
    google_rows = store.get_table_range("google_daily", "2000-01-01", today_iso)
    country_rows = store.get_table_range("sales_by_country_daily", "2000-01-01", today_iso)
    hour_rows = store.get_table_range("sales_by_hour_daily", "2000-01-01", today_iso)
    stripe_rows = store.get_table_range("stripe_daily", "2000-01-01", today_iso)
    refunds_rows = store.get_table_range("refunds_daily", "2000-01-01", today_iso)
    from src.metrics.sales_source import sales_by_source_by_month, tw_pixel_by_month
    try:
        source_rows = store.get_table_range("orders_by_source_daily", "2000-01-01", today_iso)
    except Exception:  # noqa: BLE001 — migration 014 non ancora eseguita
        source_rows = []
    try:
        tw_pixel_rows = store.get_table_range("tw_pixel_daily", "2000-01-01", today_iso)
    except Exception:  # noqa: BLE001
        tw_pixel_rows = []
    try:
        payout_rows = store.get_stripe_payouts()
        dispute_rows = store.get_stripe_disputes()
    except Exception:  # noqa: BLE001 — tabelle Stripe assenti finché non si esegue la migration
        payout_rows, dispute_rows = [], []
    return {
        "months": months,
        "units_by_month": units_by_month(units_rows),
        "meta_campaigns_by_month": meta_campaigns_by_month(meta_camp_rows),
        "google_by_month": google_by_month(google_rows),
        "sales_by_country_by_month": sales_by_country_by_month(country_rows),
        "sales_by_hour_by_month": sales_by_hour_by_month(hour_rows),
        "stripe_by_month": stripe_monthly(stripe_rows),
        "payouts_by_month": payouts_monthly(payout_rows),
        "refunds_by_month": refunds_monthly(refunds_rows),
        "disputes": dispute_rows,
        "source_by_month": sales_by_source_by_month(source_rows),
        "tw_pixel_by_month": tw_pixel_by_month(tw_pixel_rows),
    }


# --------------------------------------------------------------------------- #
# Klaviyo live (finestra piena) — CACHE PERSISTENTE + STALE-WHILE-REVALIDATE.
#
# Rate limit del reporting Klaviyo bassissimo: fetch UNA sola volta per (start,end),
# RIUSATO da tabella E breakdown (campagne + flows arrivano nello stesso oggetto), mai due
# volte nello stesso render. I mesi PASSATI, una volta presi con successo, restano in cache
# a vita di processo (non cambiano). Il mese CORRENTE ha TTL 6h ma se scaduto si SERVE il
# valore STALE mentre si tenta un refresh: se il refresh fallisce (rate limit) si continua a
# mostrare lo stale, senza bloccare né mostrare errori. Solo un COLD START senza alcun valore
# in cache può fallire -> fallback DB etichettato "(snapshot)".
#
# Store a livello di modulo: sopravvive ai rerun di Streamlit nello stesso processo.
# --------------------------------------------------------------------------- #
import time as _time  # noqa: E402

_KLA_STORE: dict = {}          # (start,end) -> {"data": kp, "ts": epoch}
_KLA_TTL = 6 * 3600            # mese corrente: freschezza 6h


def _klaviyo_fetch(start: str, end: str) -> dict:
    from src.report import load_klaviyo_period

    kp = load_klaviyo_period(start, end)
    if not kp.get("ok") or kp.get("campaigns_revenue") is None:
        raise RuntimeError(kp.get("error") or "Klaviyo period query failed")
    return kp


def _get_klaviyo(start: str, end: str, is_current: bool) -> tuple:
    """
    Ritorna (kp | None, stale: bool, error: str | None).
    - past con cache -> servito per sempre (nessun fetch).
    - current fresco -> servito.
    - current scaduto -> prova refresh; se fallisce, serve lo STALE (stale=True, no errore
      bloccante).
    - nessuna cache -> fetch (cold, sequenziale con Retry-After nel connettore); se fallisce,
      (None, False, error) -> il chiamante usa il fallback DB "(snapshot)".
    """
    key = (start, end)
    entry = _KLA_STORE.get(key)
    now = _time.time()
    if entry is not None:
        if not is_current or (now - entry["ts"] < _KLA_TTL):
            return entry["data"], False, None
        # current & stale -> refresh best-effort, altrimenti servi stale
        try:
            kp = _klaviyo_fetch(start, end)
            _KLA_STORE[key] = {"data": kp, "ts": now}
            return kp, False, None
        except Exception as exc:  # noqa: BLE001
            return entry["data"], True, str(exc)
    # cold: nessuna cache
    try:
        kp = _klaviyo_fetch(start, end)
        _KLA_STORE[key] = {"data": kp, "ts": now}
        return kp, False, None
    except Exception as exc:  # noqa: BLE001
        return None, False, str(exc)


# --------------------------------------------------------------------------- #
# Helpers di formato
# --------------------------------------------------------------------------- #
def _usd(x) -> str:
    return f"${float(x or 0):,.2f}"


def _pct(frac) -> str:
    return f"{float(frac or 0) * 100:.2f}%"


def _cvr(frac) -> str:
    """Store CVR con guardia di sanity (>10% -> 'n/a'). None/mancante -> 'n/a' (non 0.00%)."""
    from src.report import CVR_SANITY_MAX

    if frac is None:
        return "n/a"
    v = float(frac or 0)
    if v <= 0:
        return "n/a"
    return "n/a" if v > CVR_SANITY_MAX else _pct(v)


def _margin(numer, denom) -> str:
    return f"{numer / denom * 100:.1f}%" if denom else "n/a"


# --------------------------------------------------------------------------- #
# Selettore periodo
# --------------------------------------------------------------------------- #
def _select_period() -> tuple[str, str, str]:
    """Ritorna (label, start_iso, end_iso) dal selettore in cima alla pagina."""
    options = list(periods.PRESETS.keys()) + ["Custom range"]
    choice = st.radio("Period", options, horizontal=True, label_visibility="collapsed")

    if choice == "Custom range":
        today = periods.rome_today()
        c1, c2 = st.columns(2)
        start = c1.date_input("Start", value=today - timedelta(days=6), max_value=today)
        end = c2.date_input("End", value=today, max_value=today)
        start_iso, end_iso = periods.custom_range(start, end)
    else:
        start_iso, end_iso = periods.PRESETS[choice]()
    return choice, start_iso, end_iso


# --------------------------------------------------------------------------- #
# Sezioni di rendering
# --------------------------------------------------------------------------- #
def _render_period_highlights(m: dict, be: dict, meta_roas: float, be_note: str) -> None:
    """Le 5 metriche principali del periodo, EVIDENZIATE in cima."""
    st.subheader("⭐ Key figures")
    be_roas = f"{be['roas']:,.2f}x" if be.get("roas") else "n/a"
    be_cpa = _usd(be["cpa"]) if be.get("cpa") is not None else "n/a"
    r1 = st.columns(2)
    r1[0].metric("Revenue", _usd(m["revenue"]))
    r1[1].metric("Net profit (net)", _usd(m["net_profit_netto"]))
    r2 = st.columns(2)
    r2[0].metric("Break-even ROAS", be_roas)
    r2[1].metric("Break-even CPA", be_cpa)
    r3 = st.columns(2)
    r3[0].metric("Meta ROAS", f"{float(meta_roas or 0):,.2f}x" if meta_roas else "n/a")
    st.caption(f"Break-even is computed from the **{be_note}**.")


def _render_kpis(m: dict) -> None:
    """Metriche secondarie del periodo (le 5 principali sono già in evidenza sopra)."""
    st.subheader("Other metrics")
    r1 = st.columns(2)
    r1[0].metric("Orders", f"{m['num_orders']:,}")
    r1[1].metric("AOV", _usd(m["aov"]))
    r2 = st.columns(2)
    r2[0].metric("Store CVR", _cvr(m["store_cvr"]))
    r2[1].metric("Net profit (operating)", _usd(m["net_profit_operativo"]))
    r3 = st.columns(2)
    r3[0].metric("Operating margin", _margin(m["net_profit_operativo"], m["revenue"]))
    r3[1].metric("Net margin", _margin(m["net_profit_netto"], m["revenue"]))


def _render_platform(name: str, icon: str, d: dict | None) -> None:
    if not d:
        return
    st.markdown(f"**{icon} {name}**")
    cols = st.columns(2)
    cols[0].metric("Spend", _usd(d.get("spend")))
    cols[1].metric("Attributed revenue", _usd(d.get("revenue")))
    cols2 = st.columns(2)
    roas = d.get("roas") or 0
    cols2[0].metric("ROAS", f"{roas:,.2f}x")
    cpa = d.get("cpa")
    cols2[1].metric("CPA", _usd(cpa) if cpa else "n/a")


def _render_platforms(data: dict) -> None:
    st.subheader("Ad platforms")
    any_platform = any(data.get(k) for k in ("meta_daily", "tiktok_daily", "google_daily"))
    if not any_platform:
        st.caption("No ad-platform data in this period.")
    _render_platform("Meta", "📣", data.get("meta_daily"))
    _render_platform("TikTok", "🎵", data.get("tiktok_daily"))
    _render_platform("Google", "🔍", data.get("google_daily"))

    kl = data.get("klaviyo_daily")
    if kl:
        st.markdown("**✉️ Klaviyo (campaigns)**")
        cols = st.columns(2)
        cols[0].metric("Attributed revenue", _usd(kl.get("revenue")))
        cols[1].metric("Open rate", _pct(kl.get("open_rate")))


def _render_meta_campaigns(camps: list[dict]) -> None:
    if not camps:
        return
    st.subheader("Meta campaigns")
    df = pd.DataFrame([
        {
            "Campaign": c.get("campaign_name", "(no name)"),
            "Spend": round(float(c.get("spend") or 0), 2),
            "Revenue": round(float(c.get("revenue") or 0), 2),
            "Orders": int(c.get("orders") or 0),
            "ROAS": round(float(c.get("roas") or 0), 2),
            "CPA": round((c["spend"] / c["orders"]), 2) if c.get("orders") else None,
        }
        for c in camps
    ])
    st.dataframe(df, hide_index=True, use_container_width=True)


def _render_cost_breakdown(m: dict, num_days: int) -> None:
    st.subheader("Cost breakdown")
    rows = [
        ("Product COGS", m["cogs_total"]),
        (f"Shipping cost ($7 × {m['num_orders']})", m["shipping_total"]),
        ("Payment fees (7.5%)", m["payment_fees"]),
        ("Ad spend (Meta + TikTok + Google)", m["ads_spend"]),
        (f"Fixed-costs allocation (× {num_days} days)", m["fixed_cost_daily"]),
    ]
    df = pd.DataFrame(
        [{"Item": label, "Cost": f"−{_usd(val)}"} for label, val in rows]
    )
    st.dataframe(df, hide_index=True, use_container_width=True)


def _render_monthly(months: list[dict], kla: dict) -> None:
    from src.dashboard.monthly import month_label

    if not months:
        st.info("No monthly data yet.")
        return
    st.subheader("Monthly overview")

    def _mlabel(r):
        return month_label(r["month"]) + (" (partial)" if r.get("partial") else "")

    def _is_snapshot(r):
        return bool(kla.get(r["month"], {}).get("snapshot"))

    def _camp_str(r):
        k = kla.get(r["month"], {})
        v = float(k.get("campaigns_revenue") or 0.0)
        return _usd(v) + (" (snapshot)" if _is_snapshot(r) else "")

    def _flow_str(r):
        k = kla.get(r["month"], {})
        v = k.get("flows_revenue")
        if v is None:
            return "n/a" + (" (snapshot)" if _is_snapshot(r) else "")
        return _usd(float(v)) + (" (snapshot)" if _is_snapshot(r) else "")

    # Ordine colonne ESATTO richiesto. Le colonne Klaviyo sono stringhe per poter marcare
    # "(snapshot)" quando è il fallback DB (fetch live fallito).
    df = pd.DataFrame([{
        "Month": _mlabel(r),
        "Revenue": round(r["revenue"], 2),
        # Net profit e margine dallo STESSO numero (net_profit_netto): coerenti nella riga
        # e uguali a /reportlastmonth.
        "Net profit": round(r["net_profit_netto"], 2),
        "Net margin %": (round(r["net_profit_netto"] / r["revenue"] * 100, 1)
                         if r["revenue"] else None),
        "Orders": r["orders"],
        "Store CVR": _cvr(r["store_cvr"]),
        "Visitors": f"{r['visitors']:,.0f}" + (" est." if r["visitors_est"] else ""),
        "AOV": round(r["aov"], 2),
        "Meta ROAS": round(r["meta_roas"], 2),
        "Google ROAS": round(r["google_roas"], 2),
        "TikTok ROAS": round(r.get("tiktok_roas", 0.0), 2),
        "Total ad spend": round(r.get("total_ad_spend", 0.0), 2),
        "Klaviyo campaigns revenue": _camp_str(r),
        "Klaviyo flows revenue": _flow_str(r),
    } for r in months])
    st.dataframe(df, hide_index=True, use_container_width=True)
    if any(_is_snapshot(r) for r in months):
        st.caption("ℹ️ '(snapshot)' = Klaviyo live fetch failed for that month; showing the "
                   "nightly DB snapshot (may undercount).")


def _render_monthly_trend(months: list[dict]) -> None:
    """#1: BAR chart raggruppato — Revenue e Net profit per mese (da giugno '26), con label."""
    import altair as alt

    from src.dashboard.monthly import month_label

    vis = [r for r in sorted(months, key=lambda x: x["month"]) if r["month"] >= "2026-06"]
    if not vis:
        return
    st.subheader("Revenue & net profit by month")
    order = [month_label(r["month"]) for r in vis]
    rows = []
    for r in vis:
        lbl = month_label(r["month"])
        rows.append({"Month": lbl, "Metric": "Revenue",
                     "Value": round(float(r.get("revenue") or 0), 2)})
        rows.append({"Month": lbl, "Metric": "Net profit",
                     "Value": round(float(r.get("net_profit_netto") or 0), 2)})
    df = pd.DataFrame(rows)
    palette = alt.Scale(domain=["Revenue", "Net profit"], range=["#1f7a4d", "#8c9196"])
    base = alt.Chart(df).encode(
        x=alt.X("Month:N", sort=order, title=None, axis=alt.Axis(labelAngle=0)),
        xOffset=alt.XOffset("Metric:N"),
    )
    bars = base.mark_bar().encode(
        y=alt.Y("Value:Q", title="USD"),
        color=alt.Color("Metric:N", scale=palette,
                        legend=alt.Legend(orient="top", title=None)),
        tooltip=["Month", "Metric", alt.Tooltip("Value:Q", format="$,.0f")],
    )
    labels = base.mark_text(dy=-4, fontSize=11, color="#333").encode(
        y=alt.Y("Value:Q"),
        text=alt.Text("Value:Q", format="$,.0f"),
        detail="Metric:N",
    )
    st.altair_chart((bars + labels).properties(height=340), use_container_width=True)


def _render_product_units_monthly(units_by_month: dict) -> None:
    from src.dashboard.monthly import month_label
    from src.metrics.product_units import PRODUCT_KEYS, PRODUCT_KEY_LABELS

    st.subheader("Units sold per product / month")
    if not units_by_month:
        st.caption("No product-unit data yet — run /backfill to fill history.")
        return

    months = list(units_by_month.keys())
    # Tutti i bucket con vendite: le famiglie nominate (chiavi note) + i TITOLI reali dei
    # prodotti non-famiglia (nessun secchio 'Other' generico). Ordine: famiglie note prima
    # (nell'ordine canonico), poi i titoli per volume decrescente.
    all_buckets = {b for mn in months for b in units_by_month[mn]}
    family_order = [k for k, _ in PRODUCT_KEYS if k != "other" and k in all_buckets]
    title_buckets = sorted(
        (b for b in all_buckets if b not in PRODUCT_KEY_LABELS),
        key=lambda b: sum(units_by_month[mn].get(b, 0) for mn in months), reverse=True,
    )
    # 'other' residuo (titolo mancante) resta in coda se presente.
    residual = ["other"] if "other" in all_buckets else []
    buckets = family_order + title_buckets + residual

    def _label(b):
        return PRODUCT_KEY_LABELS.get(b, b)   # chiave nota -> etichetta; altrimenti è il titolo

    # TABELLA: prodotti come RIGHE, una COLONNA per mese + colonna Total.
    table_rows = []
    for b in buckets:
        row = {"Product": _label(b)}
        total = 0
        for mn in months:
            u = int(units_by_month[mn].get(b, 0))
            row[month_label(mn)] = u
            total += u
        row["Total"] = total
        table_rows.append(row)
    totals = {"Product": "Total"}
    for mn in months:
        totals[month_label(mn)] = int(sum(units_by_month[mn].get(b, 0) for b in buckets))
    totals["Total"] = int(sum(totals[month_label(mn)] for mn in months))
    table_rows.append(totals)

    st.dataframe(pd.DataFrame(table_rows), hide_index=True, use_container_width=True)


def _render_klaviyo_breakdown(months: list[dict], kla: dict) -> None:
    """
    Breakdown per-campagna Klaviyo (finestra piena). Tutti i mesi sono già stati auto-caricati
    (in cache) dal tab: qui si mostra solo il dettaglio per-campagna, senza altre chiamate API.
    """
    from src.dashboard.monthly import month_label

    st.subheader("Klaviyo campaign revenue — per-campaign breakdown")
    st.caption(
        "Full-window query per month (each campaign counted once — matches the Klaviyo "
        "dashboard). Fetched once per month and cached (past months indefinitely) to "
        "respect Klaviyo's low reporting rate limit."
    )
    for r in sorted(months, key=lambda x: x["month"], reverse=True):
        month = r["month"]
        k = kla.get(month, {})
        label = month_label(month)

        if k.get("loaded"):
            camps = sorted(k.get("campaigns") or [],
                           key=lambda c: float(c.get("revenue") or 0), reverse=True)
            total = sum(float(c.get("revenue") or 0) for c in camps)
            suffix = " · stale (refresh failed)" if k.get("stale") else ""
            with st.expander(f"{label} — ${total:,.2f} · {len(camps)} campaigns{suffix}"):
                if k.get("error"):
                    st.caption(f"⚠️ note: {k['error']}")
                if camps:
                    df = pd.DataFrame([{
                        "Campaign": c.get("campaign_name", "(no name)"),
                        "Revenue": round(float(c.get("revenue") or 0), 2),
                        "Conversions": int(c.get("conversions") or 0),
                    } for c in camps])
                    st.dataframe(df, hide_index=True, use_container_width=True)
                else:
                    st.caption("No campaigns in this window.")
        else:
            # Fetch live fallito e nessuna cache: mostra il TOTALE dallo snapshot DB.
            snap = float(k.get("campaigns_revenue") or 0.0)
            with st.expander(f"{label} — ${snap:,.2f} (snapshot)"):
                st.warning(f"Live fetch failed: {k.get('error') or 'unknown error'}. "
                           "Showing the nightly DB snapshot total; per-campaign detail "
                           "isn't stored, so it's unavailable until the live fetch succeeds.")


def _render_klaviyo_flows_monthly(months: list[dict], kla: dict) -> None:
    """#7: breakdown per-FLOW della revenue Klaviyo per mese. Fallback DB (snapshot) su errore."""
    from src.dashboard.monthly import month_label

    st.subheader("Klaviyo flow revenue — per-flow breakdown")
    for r in sorted(months, key=lambda x: x["month"], reverse=True):
        month = r["month"]
        kp = kla.get(month, {})
        label = month_label(month)
        if kp.get("loaded"):
            flows = sorted(kp.get("flows") or [],
                           key=lambda f: float(f.get("revenue") or 0), reverse=True)
            total = sum(float(f.get("revenue") or 0) for f in flows)
            suffix = " · stale" if kp.get("stale") else ""
            with st.expander(f"{label} — ${total:,.2f} · {len(flows)} flows{suffix}"):
                if flows:
                    df = pd.DataFrame([{
                        "Flow": f.get("flow_name", "(no name)"),
                        "Revenue": round(float(f.get("revenue") or 0), 2),
                        "Conversions": int(f.get("conversions") or 0),
                    } for f in flows])
                    st.dataframe(df, hide_index=True, use_container_width=True)
                else:
                    st.caption("No flow revenue in this window (or Flows:Read scope missing).")
        else:
            # Fallback DB: mostra almeno il TOTALE flows dallo snapshot notturno.
            snap = kp.get("flows_revenue")
            snap_s = f"${float(snap):,.2f}" if snap is not None else "n/a"
            with st.expander(f"{label} — {snap_s} (snapshot)"):
                st.warning("Live fetch failed; showing the nightly DB flow-revenue total "
                           "(per-flow detail unavailable until the live fetch succeeds).")


def _render_meta_campaigns_monthly(meta_by_month: dict) -> None:
    """#4: tabella campagne Meta per mese (spend, revenue, orders, ROAS, CPA)."""
    from src.dashboard.monthly import month_label

    if not meta_by_month:
        return
    st.subheader("Meta campaigns — per month")
    for month in sorted(meta_by_month, reverse=True):
        camps = meta_by_month[month]
        spend = sum(c["spend"] for c in camps)
        with st.expander(f"{month_label(month)} — spend ${spend:,.2f} · {len(camps)} campaigns"):
            df = pd.DataFrame([{
                "Campaign": c["campaign_name"],
                "Spend": round(c["spend"], 2),
                "Revenue": round(c["revenue"], 2),
                "Orders": int(c["orders"]),
                "ROAS": round(c["roas"], 2),
                "CPA": (round(c["cpa"], 2) if c["orders"] else None),
            } for c in camps])
            st.dataframe(df, hide_index=True, use_container_width=True)


def _render_google_monthly(google_by_month: dict) -> None:
    """#5: totali Google (account level, via Triple Whale) per mese."""
    if not google_by_month:
        return
    st.subheader("Google Ads — per month (account level)")
    st.caption("Google is account-level only (Triple Whale) — no per-campaign breakdown.")
    from src.dashboard.monthly import month_label

    df = pd.DataFrame([{
        "Month": month_label(mn),
        "Spend": round(g["spend"], 2),
        "Revenue": round(g["revenue"], 2),
        "Orders": int(g["orders"]),
        "ROAS": round(g["roas"], 2),
        "CPA": (round(g["cpa"], 2) if g["orders"] else None),
    } for mn, g in sorted(google_by_month.items(), reverse=True)])
    st.dataframe(df, hide_index=True, use_container_width=True)


def _render_unit_economics(months: list[dict]) -> None:
    """#8: quanto posso spendere in ads — GROSS PROFIT/ORDER, BREAK-EVEN ROAS e CPA per mese."""
    from src.dashboard.monthly import month_label, month_unit_economics

    if not months:
        return
    st.subheader("💰 How much I can spend on ads")
    st.caption("Per month, from that month's own totals: break-even CPA (max you can pay for "
               "ads per order) and break-even ROAS.")
    for r in sorted(months, key=lambda x: x["month"], reverse=True):
        u = month_unit_economics(r["revenue"], r["cogs_total"], r["orders"])
        st.markdown(f"**{month_label(r['month'])}**"
                    + (" _(partial)_" if r.get("partial") else ""))
        c1, c2 = st.columns(2)
        c1.metric("Break-even CPA (max ad $/order)",
                  _usd(u["be_cpa"]) if u["be_cpa"] is not None else "n/a")
        c2.metric("Break-even ROAS",
                  f"{u['be_roas']:,.2f}x" if u["be_roas"] else "n/a")


def _render_cost_breakdown_monthly(months: list[dict]) -> None:
    """#9: cost breakdown per mese — COGS, shipping, fees, ad spend, fixed (quota DATATA)."""
    from src.dashboard.monthly import fixed_alloc_for_month, month_label

    if not months:
        return
    st.subheader("Cost breakdown — per month")
    rows = []
    for r in sorted(months, key=lambda x: x["month"], reverse=True):
        fixed = fixed_alloc_for_month(r.get("day_strs") or [])
        rows.append({
            "Month": month_label(r["month"]),
            "COGS": round(r["cogs_total"], 2),
            "Shipping": round(r["shipping_total"], 2),
            "Payment fees": round(r["payment_fees"], 2),
            "Ad spend": round(r["total_ad_spend"], 2),
            "Fixed allocation": round(fixed, 2),
        })
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    st.caption("Fixed allocation uses the monthly fixed cost in force at each day "
               "($5,668 → $7,666 from 2026-06-11 → $6,117 from 2026-08-02), ÷30 per day.")


def _render_sales_by_location(sales_by_month: dict) -> None:
    """#10: vendite per PAESE per mese (bar chart della revenue per country)."""
    from src.dashboard.monthly import month_label

    if not sales_by_month:
        st.subheader("Sales by location — per month")
        st.caption("No country data yet — run /backfill after applying migration 010.")
        return
    st.subheader("Sales by location — per month")
    for month in sorted(sales_by_month, reverse=True):
        by_country = sales_by_month[month]
        total = sum(v["revenue"] for v in by_country.values())
        ordered = sorted(by_country.items(), key=lambda kv: kv[1]["revenue"], reverse=True)
        with st.expander(f"{month_label(month)} — ${total:,.2f} · {len(ordered)} countries"):
            chart = pd.DataFrame(
                {"Revenue": [round(v["revenue"], 2) for _c, v in ordered]},
                index=[c for c, _v in ordered],
            )
            st.bar_chart(chart, height=260)
            df = pd.DataFrame([{
                "Country": c,
                "Revenue": round(v["revenue"], 2),
                "Orders": int(v["orders"]),
                "% of month": (round(v["revenue"] / total * 100, 1) if total else 0.0),
            } for c, v in ordered])
            st.dataframe(df, hide_index=True, use_container_width=True)


def _render_weekday_profit(months: list[dict]) -> None:
    """Best/worst weekday: net profit MEDIO per giorno della settimana, per mese."""
    import altair as alt

    from src.dashboard.monthly import month_label

    if not months:
        return
    st.subheader("📅 Best vs worst weekday (avg net profit)")
    st.caption("Average net profit per weekday (months don't have equal counts of each day). "
               "Best day green, worst red.")
    for r in sorted(months, key=lambda x: x["month"], reverse=True):
        wp = [d for d in (r.get("weekday_profit") or []) if d.get("avg") is not None]
        if not wp:
            continue
        best = max(wp, key=lambda d: d["avg"])
        worst = min(wp, key=lambda d: d["avg"])
        order = [d["name"] for d in sorted(wp, key=lambda d: d["weekday"])]
        df = pd.DataFrame([{
            "Day": d["name"],
            "Avg net profit": round(d["avg"], 2),
            "Days counted": d["count"],
            "hl": ("best" if d["weekday"] == best["weekday"]
                   else "worst" if d["weekday"] == worst["weekday"] else "mid"),
        } for d in sorted(wp, key=lambda d: d["weekday"])])
        title = f"{month_label(r['month'])} — best {best['name']} / worst {worst['name']}"
        with st.expander(title):
            base = alt.Chart(df).encode(
                x=alt.X("Day:N", sort=order, title=None, axis=alt.Axis(labelAngle=0)))
            bars = base.mark_bar().encode(
                y=alt.Y("Avg net profit:Q", title="USD"),
                color=alt.Color("hl:N", scale=alt.Scale(
                    domain=["best", "mid", "worst"],
                    range=["#1f7a4d", "#9aa0a6", "#c0392b"]), legend=None),
                tooltip=["Day", alt.Tooltip("Avg net profit:Q", format="$,.0f"),
                         "Days counted"],
            )
            labels = base.mark_text(dy=-4, fontSize=10, color="#333").encode(
                y=alt.Y("Avg net profit:Q"),
                text=alt.Text("Avg net profit:Q", format="$,.0f"))
            st.altair_chart((bars + labels).properties(height=260),
                            use_container_width=True)


def _render_sales_by_hour(sales_hour_by_month: dict) -> None:
    """Best/worst hour: revenue (e ordini) per ORA del giorno (Europe/Rome), per mese."""
    import altair as alt

    from src.dashboard.monthly import month_label

    from src.metrics.sales_timing import remap_hours

    st.subheader("🕒 Best vs worst hour (by order count)")
    if not sales_hour_by_month:
        st.caption("No hourly data yet — run /backfill after applying migration 011.")
        return
    # Toggle fuso SOLO per la visualizzazione (i dati restano salvati in Europe/Rome).
    # La scelta è persistita in session_state (key) e vale per TUTTI i mesi insieme.
    tz_label = st.radio("Timezone", ["Rome", "Dubai"], horizontal=True, key="hour_tz")
    tz_name = {"Rome": "Europe/Rome", "Dubai": "Asia/Dubai"}[tz_label]
    st.caption(f"Orders by hour of day (**{tz_label}**, remapped from stored Rome buckets). "
               "Best/worst = most/fewest orders in that hour across the month; revenue in "
               "the tooltip.")
    for month in sorted(sales_hour_by_month, reverse=True):
        by_hour = remap_hours(sales_hour_by_month[month], month, tz_name)
        present = {h: by_hour.get(h, {"revenue": 0.0, "orders": 0}) for h in range(24)}
        ord_hours = {h: v["orders"] for h, v in present.items() if v["orders"] > 0}
        if not ord_hours:
            continue
        best = max(ord_hours, key=ord_hours.get)    # più ordini
        worst = min(ord_hours, key=ord_hours.get)   # meno ordini (tra le ore con vendite)
        total_orders = sum(v["orders"] for v in present.values())
        df = pd.DataFrame([{
            "Hour": f"{h:02d}", "Orders": present[h]["orders"],
            "Revenue": round(present[h]["revenue"], 2),
            "hl": ("best" if h == best else "worst" if h == worst else "mid"),
        } for h in range(24)])
        title = (f"{month_label(month)} — {total_orders:,} orders · "
                 f"best {best:02d}:00 / worst {worst:02d}:00")
        with st.expander(title):
            chart = alt.Chart(df).mark_bar().encode(
                x=alt.X("Hour:N", sort=[f"{h:02d}" for h in range(24)],
                        title=f"Hour ({tz_label})",
                        axis=alt.Axis(labelAngle=0, labelOverlap=True)),
                y=alt.Y("Orders:Q", title="Orders"),
                color=alt.Color("hl:N", scale=alt.Scale(
                    domain=["best", "mid", "worst"],
                    range=["#1f7a4d", "#9aa0a6", "#c0392b"]), legend=None),
                tooltip=["Hour", "Orders", alt.Tooltip("Revenue:Q", format="$,.0f")],
            )
            st.altair_chart(chart.properties(height=260), use_container_width=True)
            top = (df[df["Orders"] > 0].sort_values("Orders", ascending=False)
                   [["Hour", "Orders", "Revenue"]])
            st.dataframe(top, hide_index=True, use_container_width=True)


def _render_stripe_money(monthly: dict) -> None:
    """Sezione Stripe / Money: riconciliazione, fee reali vs 7.5%, refund, dispute."""
    from src.dashboard.monthly import month_label
    from src.metrics.stripe_metrics import (
        dispute_rate,
        reconciliation_row,
        total_payment_cost_rate,
    )

    st.subheader("💳 Stripe / Money")
    stripe_m = monthly.get("stripe_by_month") or {}
    payouts_m = monthly.get("payouts_by_month") or {}
    refunds_m = monthly.get("refunds_by_month") or {}
    disputes = monthly.get("disputes") or []
    months = monthly.get("months") or []
    rev_by_month = {r["month"]: r["revenue"] for r in months}

    if not stripe_m and not refunds_m and not disputes:
        st.caption("No Stripe data yet — run migrations 012/013, add STRIPE_API_KEY, then "
                   "/backfill_stripe and /backfill.")
        return

    # 1) Riconciliazione: Shopify revenue vs Stripe gross vs Stripe net vs payout.
    st.markdown("**Reconciliation — Shopify vs Stripe vs payouts**")
    rec_rows = []
    for mn in sorted(set(rev_by_month) | set(stripe_m), reverse=True):
        s = stripe_m.get(mn, {})
        rc = reconciliation_row(rev_by_month.get(mn, 0.0), s.get("gross", 0.0),
                                s.get("net", 0.0), payouts_m.get(mn, 0.0))
        rec_rows.append({
            "Month": month_label(mn),
            "Shopify rev": round(rc["shopify_revenue"], 2),
            "Stripe gross": round(rc["stripe_gross"], 2),
            "Stripe net": round(rc["stripe_net"], 2),
            "Payouts": round(rc["payouts"], 2),
            "Gross−Rev": round(rc["diff"], 2),
            "Diff %": (round(rc["diff_pct"], 1) if rc["diff_pct"] is not None else None),
        })
    st.dataframe(pd.DataFrame(rec_rows), hide_index=True, use_container_width=True)
    st.caption("Stripe gross < Shopify revenue is expected (PayPal share doesn't flow through "
               "Stripe). Payouts can differ from net by timing (money arrives days later).")

    # 2) Costo di pagamento REALE vs stima: fee Stripe + surcharge Shopify (invisibile a Stripe).
    surcharge = settings.SHOPIFY_GATEWAY_SURCHARGE_PCT
    est_pct = settings.FEE_PAGAMENTI * 100
    st.markdown(f"**Real payment cost vs {est_pct:.1f}% assumption** — "
                f"Stripe fee + Shopify gateway surcharge ({surcharge*100:.2f}%)")
    fee_rows = []
    for mn in sorted(stripe_m, reverse=True):
        s = stripe_m[mn]
        rates = total_payment_cost_rate(s.get("gross", 0.0), s.get("fee", 0.0))
        sr, tot = rates["stripe_rate"], rates["total_rate"]
        fee_rows.append({
            "Month": month_label(mn),
            "Gross": round(s.get("gross", 0.0), 2),
            "Fee": round(s.get("fee", 0.0), 2),
            "Stripe %": (round(sr * 100, 2) if sr is not None else None),
            "Shopify surcharge %": round(surcharge * 100, 2),
            "Total %": (round(tot * 100, 2) if tot is not None else None),
            "Assumption %": round(est_pct, 2),
            # Il flag è sul TOTALE, non sulla sola fee Stripe.
            f"Δ vs {est_pct:.1f}%": (round(tot * 100 - est_pct, 2) if tot is not None else None),
        })
    if fee_rows:
        st.dataframe(pd.DataFrame(fee_rows), hide_index=True, use_container_width=True)
        if surcharge == 0:
            st.caption("⚠️ SHOPIFY_GATEWAY_SURCHARGE_PCT is 0 — set it to your Shopify plan's "
                       "gateway surcharge. Stripe's fee alone understates your true payment cost, "
                       "so the comparison against the assumption is not yet meaningful.")
        else:
            st.caption("Total = real Stripe fee + Shopify gateway surcharge (billed by Shopify, "
                       "invisible to Stripe). The mismatch flag is on the TOTAL, not Stripe alone.")

    # 3) Refund mensili (da Shopify; % della revenue). Revenue già li netta -> solo visibilità.
    st.markdown("**Refunds (Shopify, incl. PayPal) — visibility only**")
    ref_rows = []
    for mn in sorted(refunds_m, reverse=True):
        rf = refunds_m[mn]
        rev = rev_by_month.get(mn, 0.0)
        ref_rows.append({
            "Month": month_label(mn),
            "Refunds": round(rf.get("amount", 0.0), 2),
            "Count": int(rf.get("count", 0)),
            "% of revenue": (round(rf["amount"] / rev * 100, 1) if rev else None),
        })
    if ref_rows:
        st.dataframe(pd.DataFrame(ref_rows), hide_index=True, use_container_width=True)
    else:
        st.caption("No refund data yet — run /backfill after migration 013.")

    # 4) Dispute: lista con stato + scadenza evidenze + tasso mensile (flag verso 1%).
    st.markdown("**Disputes**")
    if not disputes:
        st.caption("No disputes 🎉")
    else:
        _badge = {"needs_response": "🔴", "warning_needs_response": "🟠",
                  "under_review": "🟡", "won": "🟢", "lost": "⚫️", "charge_refunded": "⚪️"}
        drows = []
        for d in sorted(disputes, key=lambda x: x.get("created") or "", reverse=True):
            drows.append({
                "": _badge.get(str(d.get("status")), "•"),
                "Created": d.get("created"),
                "Amount": round(float(d.get("amount") or 0), 2),
                "Status": d.get("status"),
                "Reason": d.get("reason"),
                "Evidence due": d.get("evidence_due") or "—",
            })
        st.dataframe(pd.DataFrame(drows), hide_index=True, use_container_width=True)
        # tasso dispute mensile = dispute create nel mese / charge del mese
        drate_rows = []
        disp_by_month: dict[str, int] = {}
        for d in disputes:
            mkey = str(d.get("created") or "")[:7]
            if mkey:
                disp_by_month[mkey] = disp_by_month.get(mkey, 0) + 1
        for mn in sorted(disp_by_month, reverse=True):
            charges = int((stripe_m.get(mn, {}) or {}).get("charge_count", 0))
            dr = dispute_rate(disp_by_month[mn], charges)
            drate_rows.append({
                "Month": month_label(mn),
                "Disputes": disp_by_month[mn],
                "Charges": charges,
                "Rate %": (round(dr * 100, 3) if dr is not None else None),
                "⚠️": ("APPROACHING 1%" if dr is not None and dr >= 0.008 else ""),
            })
        if drate_rows:
            st.caption("Monthly dispute rate (disputes ÷ charges) — Stripe flags accounts above 1%.")
            st.dataframe(pd.DataFrame(drate_rows), hide_index=True, use_container_width=True)


def _render_goals(months: list[dict]) -> None:
    """#11: obiettivi mensili (statici) + avanzamento del mese corrente vs obiettivo."""
    from src.dashboard.monthly import MONTHLY_GOALS, goal_progress, month_label

    st.subheader("🎯 Goals")

    # Avanzamento del mese corrente (se ha un obiettivo).
    current = next((r for r in months if r.get("is_current")), None)
    if current and current["month"] in MONTHLY_GOALS:
        import calendar

        g = MONTHLY_GOALS[current["month"]]
        y, mo = (int(x) for x in current["month"].split("-"))
        days_in_month = calendar.monthrange(y, mo)[1]
        day_of_month = int(max(current.get("day_strs") or [current["month"] + "-01"])[8:10])
        p = goal_progress(current["revenue"], g["goal"], day_of_month, days_in_month)
        st.markdown(f"**{month_label(current['month'])} — progress vs ${g['goal']:,} goal**")
        c1, c2, c3 = st.columns(3)
        c1.metric("Revenue so far", _usd(current["revenue"]),
                  f"{p['pct']:.1f}% of goal" if p["pct"] is not None else None)
        c2.metric("Pace ($/day)", _usd(p["actual_per_day"]),
                  f"need {_usd(p['needed_per_day'])}/day" if p["needed_per_day"] else None)
        c3.metric("Projected month-end", _usd(p["projected"]),
                  "on pace ✅" if p["on_pace"] else "behind ⚠️")
        st.progress(min((p["pct"] or 0) / 100.0, 1.0))

    df = pd.DataFrame([{
        "Month": month_label(mn),
        "Goal": f"${g['goal']:,}",
        "Per day": f"${g['per_day']:,}",
        "Orders/day": g["orders_per_day"],
    } for mn, g in MONTHLY_GOALS.items()])
    st.dataframe(df, hide_index=True, use_container_width=True)


_SOURCE_LABELS = {
    "meta": "Meta (incl. unattributed)", "google_paid": "Google (paid)",
    "google_organic": "Google (organic)", "tiktok": "TikTok", "pinterest": "Pinterest",
    "email": "Email/Klaviyo", "direct": "Direct",
}


def _source_label(source: str) -> str:
    return _SOURCE_LABELS.get(source, (source or "other").replace("_", " ").title())


_ROLLUP_LABELS = {
    "FB ads": "FB/IG ads (incl. organic — unsplittable until ad UTMs)",
    "Google ads": "Google ads",
    "Klaviyo": "Klaviyo",
    "Organic": "Organic",
}


def _render_rollup_cards(by_source: dict) -> None:
    """Quattro card headline FB ads / Google ads / Klaviyo / Organic (last-click), % del totale."""
    from src.metrics.sales_source import rollup_groups

    roll = rollup_groups(by_source)
    if not any(r["revenue"] or r["orders"] for r in roll):
        return
    cols = st.columns(4)
    for col, r in zip(cols, roll):
        col.metric(_ROLLUP_LABELS.get(r["group"], r["group"]), f"${r['revenue']:,.0f}",
                   f"{r['pct']:.0f}% · {r['orders']} ord", delta_color="off")
    st.caption("Rollup = **last-click view only**. FB/IG ads = the Meta bucket (ads have no UTM "
               "yet, so paid + organic Meta are merged). Organic = direct + search engines + "
               "TikTok + Pinterest + other. Meta/Google's own claim & TW pixel are in the "
               "three-way table below, not here.")


def _last_click_df(by_source: dict) -> "pd.DataFrame":
    """DataFrame ordinato per revenue con % sul totale (last-click)."""
    from src.metrics.sales_source import aggregate_sources

    rows = aggregate_sources(by_source)
    return pd.DataFrame([{
        "Source": _source_label(r["source"]),
        "Orders": r["orders"],
        "Revenue": r["revenue"],
        "% of total": r["pct"],
    } for r in rows])


def _tw_col_label(tw_pixel: dict) -> str:
    """Etichetta ONESTA della colonna TW: 'TW pixel' solo se i valori vengono da metriche pixel;
    altrimenti 'TW (platform-reported)'."""
    kinds = {(v.get("kind") or "platform-reported")
             for k, v in (tw_pixel or {}).items() if k in ("meta", "google", "tiktok")}
    if kinds and kinds == {"pixel"}:
        return "TW pixel"
    if not kinds:
        return "TW"
    return "TW (platform-reported)"


def _three_way_df(meta_self: dict, google_self: dict, tw_pixel: dict, by_source: dict,
                  tw_label: str) -> "pd.DataFrame":
    """Confronto a 3 vie per Meta e Google: auto-attribuzione piattaforma / TW / last-click."""
    twm, twg = (tw_pixel.get("meta") or {}), (tw_pixel.get("google") or {})
    lc_meta = by_source.get("meta") or {}
    lc_gp = by_source.get("google_paid") or {}
    rows = [
        {"Channel": "Meta",
         "Platform claims $": round(float(meta_self.get("revenue") or 0), 2),
         f"{tw_label} $": round(float(twm.get("revenue") or 0), 2),
         "Last-click $": round(float(lc_meta.get("revenue") or 0), 2),
         "Platform ord": int(meta_self.get("orders") or 0),
         f"{tw_label} ord": round(float(twm.get("orders") or 0), 1),
         "Last-click ord": int(lc_meta.get("orders") or 0)},
        {"Channel": "Google",
         "Platform claims $": round(float(google_self.get("revenue") or 0), 2),
         f"{tw_label} $": round(float(twg.get("revenue") or 0), 2),
         "Last-click $": round(float(lc_gp.get("revenue") or 0), 2),
         "Platform ord": int(google_self.get("orders") or 0),
         f"{tw_label} ord": round(float(twg.get("orders") or 0), 1),
         "Last-click ord": int(lc_gp.get("orders") or 0)},
    ]
    return pd.DataFrame(rows)


_SOURCE_CAVEAT = (
    "⚠️ **Last-click** = where the order landed (Shopify utm/referrer). It **undercounts ads** "
    "vs the platform's own claim (view-through, longer windows, untagged clicks). Read it as "
    "*landing source*, not ad effectiveness. Compare the three methods below — the gap is "
    "expected. Organic / direct / email come only from last-click."
)


def _render_three_way(meta_self: dict, google_self: dict, tw_pixel: dict, by_source: dict) -> None:
    has_any = any([meta_self.get("revenue"), google_self.get("revenue"),
                   tw_pixel, by_source.get("meta"), by_source.get("google_paid")])
    if not has_any:
        return
    tw_label = _tw_col_label(tw_pixel)
    st.markdown(f"**Three-way attribution — Meta claims / {tw_label} / last-click**")
    st.dataframe(_three_way_df(meta_self, google_self, tw_pixel, by_source, tw_label),
                 hide_index=True, use_container_width=True)
    if tw_label == "TW (platform-reported)":
        st.caption("ℹ️ TW column shows **platform-reported** numbers via Triple Whale "
                   "(facebookPurchases / ga_all_transactions / tiktokPurchases), not TW's own "
                   "per-channel pixel — those metrics aren't exposed on this account.")
    total = tw_pixel.get("pixel_total") or {}
    if total.get("orders") or total.get("revenue"):
        st.caption(f"TW total pixel-attributed (all channels): "
                   f"{float(total.get('orders') or 0):,.0f} orders · "
                   f"${float(total.get('revenue') or 0):,.0f}")
    if not tw_pixel:
        st.caption("TW column empty: no Triple Whale attribution metrics found yet (nightly only).")


def _render_sales_source_period(data: dict) -> None:
    """Sezione Sorgenti (Period tab): last-click + confronto a 3 vie."""
    by_source = data.get("source_agg") or {}
    tw_pixel = data.get("tw_pixel_agg") or {}
    meta_self = data.get("meta_self") or {}
    google_self = data.get("google_self") or {}
    if not by_source and not tw_pixel and not meta_self.get("revenue"):
        return
    st.subheader("🧭 Sales by source")
    st.caption("Last-click (order landing data)")
    if by_source:
        _render_rollup_cards(by_source)                     # headline Paid / Organic / Email
    st.markdown(_SOURCE_CAVEAT)
    if by_source:
        df = _last_click_df(by_source)
        st.dataframe(df, hide_index=True, use_container_width=True)
        st.bar_chart(df.set_index("Source")["Revenue"], height=260)
    else:
        st.caption("No last-click data for this period yet (run /backfill after migration 014).")
    _render_three_way(meta_self, google_self, tw_pixel, by_source)


def _render_sales_source_monthly(monthly: dict) -> None:
    """Sezione Sorgenti (Monthly tab): last-click + confronto a 3 vie, per mese."""
    from src.dashboard.monthly import month_label

    source_by_month = monthly.get("source_by_month") or {}
    tw_pixel_by_month = monthly.get("tw_pixel_by_month") or {}
    months = monthly.get("months") or []
    self_by_month = {r["month"]: r for r in months}
    if not source_by_month and not tw_pixel_by_month:
        st.subheader("🧭 Sales by source — per month")
        st.caption("No source data yet — run /backfill after applying migration 014.")
        return
    st.subheader("🧭 Sales by source — per month")
    st.caption("Last-click (order landing data)")
    st.markdown(_SOURCE_CAVEAT)
    all_months = sorted(set(source_by_month) | set(tw_pixel_by_month), reverse=True)
    for month in all_months:
        by_source = source_by_month.get(month) or {}
        total = sum(v["revenue"] for v in by_source.values()) if by_source else 0.0
        with st.expander(f"{month_label(month)} — ${total:,.2f} · {len(by_source)} sources"):
            if by_source:
                _render_rollup_cards(by_source)             # headline Paid / Organic / Email
                df = _last_click_df(by_source)
                st.dataframe(df, hide_index=True, use_container_width=True)
                st.bar_chart(df.set_index("Source")["Revenue"], height=240)
            mr = self_by_month.get(month) or {}
            _render_three_way(mr.get("meta_self") or {}, mr.get("google_self") or {},
                              tw_pixel_by_month.get(month) or {}, by_source)


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def _render_period_tab() -> None:
    label, start_iso, end_iso = _select_period()
    # "Today" -> pull LIVE (come /today): il DB ha i dati solo dopo il cron notturno.
    is_today_live = (label == "Today")
    if is_today_live:
        st.caption(f"**Today (live, provisional attribution)** · {start_iso} · USD · "
                   "live pull, cached 5 min · ad revenue/ROAS settle later")
    else:
        st.caption(f"**{label}** · {start_iso} → {end_iso} · USD · read-only (nightly data)")
    try:
        data = _compute_today_live() if is_today_live else _compute_period(start_iso, end_iso)
    except Exception as exc:  # noqa: BLE001
        st.error(f"Could not load data: {exc}")
        return
    if not data:
        st.info("No data for the selected period yet.")
        return

    m = data["metrics"]
    be = data["breakeven"]
    meta_roas = float((data.get("meta_daily") or {}).get("roas") or 0.0)
    num_days = data["num_days"]
    # Il break-even usa gli ultimi 4 giorni del periodo (per un solo giorno = quel giorno).
    be_note = "selected day (own day)" if num_days == 1 else "last 4 days of the period"

    _render_period_highlights(m, be, meta_roas, be_note)     # le 5 metriche in evidenza
    _render_kpis(m)                                          # metriche secondarie
    # (rimossi i grafici "Revenue & net profit trend" e "Daily trends — AOV & break-even")
    _render_platforms(data)
    _render_meta_campaigns(data["meta_campaigns"])
    _render_sales_source_period(data)                       # sorgenti last-click + 3 vie
    _render_cost_breakdown(m, data["num_days"])


def _render_monthly_tab() -> None:
    try:
        monthly = _compute_monthly()
    except Exception as exc:  # noqa: BLE001
        st.error(f"Could not load monthly data: {exc}")
        return
    if not monthly:
        st.info("No monthly data yet.")
        return
    months = monthly["months"]

    # Klaviyo (finestra piena) per OGNI mese visibile — UNA sola fetch condivisa da tabella e
    # breakdown (campagne + flows nello stesso oggetto). Store persistente + stale-while-
    # revalidate (vedi _get_klaviyo): cold start sequenziale con Retry-After, poi cache.
    from src.dashboard.monthly import month_label as _ml

    kla: dict[str, dict] = {}
    # Ordine di FETCH: mese corrente PRIMA (più importante e più a rischio rate-limit se
    # ultimo), poi gli altri. L'ordine di RENDER non dipende da questo (kla è per mese).
    fetch_order = sorted(months, key=lambda r: (not r.get("is_current"), r["month"]))
    for r in fetch_order:
        month = r["month"]
        # DB fallback (snapshot notturno) per campagne+flows di questo mese.
        db_camp = float(r.get("klaviyo_campaigns_revenue") or 0.0)
        db_flow = r.get("klaviyo_flows_revenue")
        placeholder = st.empty()
        with placeholder, st.spinner(f"Loading Klaviyo — {_ml(month)}…"):
            kp, stale, err = _get_klaviyo(r["klaviyo_start"], r["klaviyo_end"], r["is_current"])
        placeholder.empty()
        if kp is not None:
            kla[month] = {
                "loaded": True, "stale": stale,
                "campaigns_revenue": float(kp.get("campaigns_revenue") or 0.0),
                "flows_revenue": kp.get("flows_revenue"),
                "campaigns": kp.get("campaigns") or [],
                "flows": kp.get("flows") or [],
                "error": err or kp.get("error"),   # stale-error o errore SOLO-flows
            }
        else:
            # nessuna cache e fetch fallito -> fallback DB, ETICHETTATO "(snapshot)".
            kla[month] = {
                "loaded": False, "stale": False, "snapshot": True,
                "campaigns_revenue": db_camp,
                "flows_revenue": db_flow,   # anche i flows fanno fallback al DB
                "campaigns": [], "flows": [], "error": err,
            }

    # Ordine sezioni richiesto:
    _render_monthly_trend(months)                                        # 1 (per MESE)
    _render_monthly(months, kla)                                          # 2
    _render_product_units_monthly(monthly["units_by_month"])              # 3
    _render_meta_campaigns_monthly(monthly.get("meta_campaigns_by_month") or {})   # 4
    _render_google_monthly(monthly.get("google_by_month") or {})          # 5
    _render_klaviyo_breakdown(months, kla)                                # 6
    _render_klaviyo_flows_monthly(months, kla)                            # 7
    _render_unit_economics(months)                                       # 8
    _render_cost_breakdown_monthly(months)                               # 9
    _render_sales_by_location(monthly.get("sales_by_country_by_month") or {})   # 10
    _render_sales_source_monthly(monthly)                                 # sorgenti (Fase 9)
    _render_weekday_profit(months)                                        # best/worst weekday
    _render_sales_by_hour(monthly.get("sales_by_hour_by_month") or {})    # best/worst hour
    _render_stripe_money(monthly)                                         # Stripe / Money
    _render_goals(months)                                                # 11


def main() -> None:
    if not _check_password():
        st.stop()

    st.title("📊 Heritage Ring")
    tab_period, tab_monthly = st.tabs(["📅 Period", "🗓️ Monthly"])
    with tab_period:
        _render_period_tab()
    with tab_monthly:
        _render_monthly_tab()

    if st.button("🔄 Refresh data"):
        st.cache_data.clear()
        st.rerun()


# Streamlit esegue lo script top-level ad ogni interazione.
main()
