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
            "revenue": m.revenue, "orders": m.num_orders, "aov": m.aov,
            # CVR mensile = ordini ÷ sessioni reali (None -> "n/a", non 0.00%)
            "store_cvr": monthly_store_cvr(rows),
            "visitors": visitors, "visitors_est": est,
            "meta_roas": float((meta_daily or {}).get("roas") or 0.0),
            "google_roas": float((google_daily or {}).get("roas") or 0.0),
            "tiktok_roas": float((tiktok_daily or {}).get("roas") or 0.0),
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

    units_rows = store.get_table_range("product_units_daily", "2000-01-01", today_iso)
    meta_camp_rows = store.get_table_range("meta_campaigns", "2000-01-01", today_iso)
    google_rows = store.get_table_range("google_daily", "2000-01-01", today_iso)
    country_rows = store.get_table_range("sales_by_country_daily", "2000-01-01", today_iso)
    return {
        "months": months,
        "units_by_month": units_by_month(units_rows),
        "meta_campaigns_by_month": meta_campaigns_by_month(meta_camp_rows),
        "google_by_month": google_by_month(google_rows),
        "sales_by_country_by_month": sales_by_country_by_month(country_rows),
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
        "Net profit": round(r["net_profit_operativo"], 2),
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
                     "Value": round(float(r.get("net_profit_operativo") or 0), 2)})
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


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def _render_period_tab() -> None:
    label, start_iso, end_iso = _select_period()
    st.caption(f"**{label}** · {start_iso} → {end_iso} · USD · read-only (nightly data)")
    try:
        data = _compute_period(start_iso, end_iso)
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
