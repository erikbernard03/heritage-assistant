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
    # viste live a finestra piena si caricano a parte, in cache e in modo lazy (vedi
    # _klaviyo_period_cached e _render_monthly_tab).
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
# Klaviyo live (finestra piena) — CACHED per non sforare il rate limit basso.
# I mesi PASSATI non cambiano -> cache lunga; il mese CORRENTE -> TTL 6h.
# La funzione RILANCIA in caso di fallimento, così i fallimenti NON vengono messi in
# cache (verranno ritentati) e un cache-hit è sempre un successo (nessun warning).
# --------------------------------------------------------------------------- #
def _klaviyo_or_raise(start: str, end: str) -> dict:
    from src.report import load_klaviyo_period

    kp = load_klaviyo_period(start, end)
    if not kp.get("ok") or kp.get("campaigns_revenue") is None:
        raise RuntimeError(kp.get("error") or "Klaviyo period query failed")
    return kp


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def _klaviyo_recent(start: str, end: str) -> dict:
    """Mese corrente: cache 6h (cambia durante il giorno)."""
    return _klaviyo_or_raise(start, end)


@st.cache_data(ttl=None, show_spinner=False)
def _klaviyo_archived(start: str, end: str) -> dict:
    """Mesi passati: non cambiano -> cache per tutta la vita del processo."""
    return _klaviyo_or_raise(start, end)


def _klaviyo_period_cached(start: str, end: str, is_current: bool) -> dict:
    """Risultato Klaviyo a finestra piena, cache-ato per (start, end). Rilancia sull'errore."""
    return (_klaviyo_recent if is_current else _klaviyo_archived)(start, end)


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
def _render_kpis(m: dict, be: dict) -> None:
    st.subheader("Key metrics")
    r1 = st.columns(2)
    r1[0].metric("Revenue", _usd(m["revenue"]))
    r1[1].metric("Orders", f"{m['num_orders']:,}")
    r2 = st.columns(2)
    r2[0].metric("AOV", _usd(m["aov"]))
    r2[1].metric("Store CVR", _cvr(m["store_cvr"]))
    r3 = st.columns(2)
    r3[0].metric("Net profit (operating)", _usd(m["net_profit_operativo"]))
    r3[1].metric("Net profit (net)", _usd(m["net_profit_netto"]))
    r4 = st.columns(2)
    r4[0].metric("Operating margin", _margin(m["net_profit_operativo"], m["revenue"]))
    r4[1].metric("Net margin", _margin(m["net_profit_netto"], m["revenue"]))
    r5 = st.columns(2)
    be_roas = f"{be['roas']:,.2f}x" if be.get("roas") else "n/a"
    be_cpa = _usd(be["cpa"]) if be.get("cpa") is not None else "n/a"
    r5[0].metric("Break-even ROAS", be_roas)
    r5[1].metric("Break-even CPA", be_cpa)


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


def _render_trend(daily_rows: list[dict]) -> None:
    st.subheader("Revenue & net profit trend")
    df = pd.DataFrame([
        {
            "day": r["day"],
            "Revenue": round(float(r.get("revenue") or 0), 2),
            "Net profit (operating)": round(float(r.get("net_profit_operativo") or 0), 2),
        }
        for r in daily_rows
    ]).set_index("day")
    st.bar_chart(df["Revenue"], color="#2563eb", height=220)
    st.line_chart(df["Net profit (operating)"], color="#16a34a", height=220)


def _render_breakeven_trends(be_series: list[dict]) -> None:
    """Grafici giornalieri: AOV, break-even ROAS, break-even CPA (4-day rolling, come report)."""
    if not be_series:
        return
    st.subheader("Daily trends — AOV & break-even")
    df = pd.DataFrame(be_series).set_index("day")
    st.caption("AOV ($/order)")
    st.line_chart(df["aov"], color="#7c3aed", height=200)
    st.caption("Break-even ROAS (×) — from each day's prior 4-day window")
    st.line_chart(df["be_roas"], color="#ea580c", height=200)
    st.caption("Break-even CPA ($)")
    st.line_chart(df["be_cpa"], color="#0891b2", height=200)


def _render_monthly(months: list[dict], kla: dict) -> None:
    from src.dashboard.monthly import month_label

    if not months:
        st.info("No monthly data yet.")
        return
    st.subheader("Monthly overview")

    def _mlabel(r):
        return month_label(r["month"]) + (" (partial)" if r.get("partial") else "")

    def _camp(r):
        k = kla.get(r["month"], {})
        return float(k["campaigns_revenue"]) if k.get("loaded") else float(r["klaviyo_campaigns_revenue"] or 0.0)

    def _flow(r):
        k = kla.get(r["month"], {})
        v = k.get("flows_revenue") if k.get("loaded") else r.get("klaviyo_flows_revenue")
        return (float(v) if v is not None else None)

    # Ordine colonne ESATTO richiesto.
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
        "Klaviyo campaigns revenue": round(_camp(r), 2),
        "Klaviyo flows revenue": (round(_flow(r), 2) if _flow(r) is not None else None),
    } for r in months])
    st.dataframe(df, hide_index=True, use_container_width=True)
    if any(not kla.get(r["month"], {}).get("loaded") for r in months):
        st.caption("ℹ️ Klaviyo values for some months couldn't be fetched live "
                   "(rate limit / error) — showing nightly DB snapshots for those.")


def _render_monthly_trend(months: list[dict]) -> None:
    """#1: Revenue e Net profit aggregati PER MESE (un punto per mese, due serie)."""
    from src.dashboard.monthly import month_label

    if not months:
        return
    st.subheader("Revenue & net profit over time")
    df = pd.DataFrame([{
        "Month": month_label(r["month"]),
        "Revenue": round(float(r.get("revenue") or 0), 2),
        "Net profit": round(float(r.get("net_profit_operativo") or 0), 2),
    } for r in sorted(months, key=lambda x: x["month"])]).set_index("Month")
    st.line_chart(df, color=["#1f7a4d", "#8c9196"], height=300)


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
            with st.expander(f"{label} — ${total:,.2f} · {len(camps)} campaigns"):
                if k.get("error"):
                    st.caption(f"⚠️ flows note: {k['error']}")
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
            # Fallimento live: il TABLE usa lo snapshot DB per questo mese. Nessun bottone —
            # il fetch è auto e i fallimenti non sono in cache, quindi si ritenta da solo al
            # prossimo caricamento della pagina.
            with st.expander(f"{label} — live fetch failed (showing DB snapshot in table)"):
                st.warning(f"Klaviyo live fetch failed: {k.get('error') or 'unknown error'}")


def _render_klaviyo_flows_monthly(months: list[dict], kla: dict) -> None:
    """#7: breakdown per-FLOW della revenue Klaviyo per mese (dai dati live in cache)."""
    from src.dashboard.monthly import month_label

    if not any(kla.get(r["month"], {}).get("loaded") for r in months):
        return
    st.subheader("Klaviyo flow revenue — per-flow breakdown")
    for r in sorted(months, key=lambda x: x["month"], reverse=True):
        month = r["month"]
        kp = kla.get(month, {})
        if not kp.get("loaded"):
            continue
        flows = sorted(kp.get("flows") or [],
                       key=lambda f: float(f.get("revenue") or 0), reverse=True)
        total = sum(float(f.get("revenue") or 0) for f in flows)
        with st.expander(f"{month_label(month)} — ${total:,.2f} · {len(flows)} flows"):
            if flows:
                df = pd.DataFrame([{
                    "Flow": f.get("flow_name", "(no name)"),
                    "Revenue": round(float(f.get("revenue") or 0), 2),
                    "Conversions": int(f.get("conversions") or 0),
                } for f in flows])
                st.dataframe(df, hide_index=True, use_container_width=True)
            else:
                st.caption("No flow revenue in this window (or Flows:Read scope missing).")


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
    st.caption("Per month, from that month's own totals: gross profit per order, break-even "
               "ROAS, and break-even CPA (the max you can pay for ads per order).")
    for r in sorted(months, key=lambda x: x["month"], reverse=True):
        u = month_unit_economics(r["revenue"], r["cogs_total"], r["orders"])
        st.markdown(f"**{month_label(r['month'])}**"
                    + (" _(partial)_" if r.get("partial") else ""))
        c1, c2, c3 = st.columns(3)
        c1.metric("Gross profit / order",
                  _usd(u["gross_per_order"]) if u["gross_per_order"] is not None else "n/a")
        c2.metric("Break-even ROAS",
                  f"{u['be_roas']:,.2f}x" if u["be_roas"] else "n/a")
        c3.metric("Break-even CPA (max ad $/order)",
                  _usd(u["be_cpa"]) if u["be_cpa"] is not None else "n/a")


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
    _render_kpis(m, data["breakeven"])
    _render_trend(data["daily_rows"])
    _render_breakeven_trends(data["be_series"])
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

    # Klaviyo (finestra piena) per OGNI mese visibile, AUTO-CARICATO e CACHED. I mesi passati
    # sono in cache indefinita (non cambiano): al COLD START ognuno viene tirato UNA volta, in
    # sequenza, rispettando il Retry-After -> il rate limit non viene mai colpito; dopo è tutto
    # cache (zero chiamate). Uno spinner per mese appare solo mentre il fetch è in corso.
    from src.dashboard.monthly import month_label as _ml

    kla: dict[str, dict] = {}
    for r in months:
        month = r["month"]
        placeholder = st.empty()
        try:
            with placeholder, st.spinner(f"Loading Klaviyo — {_ml(month)}…"):
                kp = _klaviyo_period_cached(r["klaviyo_start"], r["klaviyo_end"], r["is_current"])
            kla[month] = {
                "loaded": True,
                "campaigns_revenue": float(kp.get("campaigns_revenue") or 0.0),
                "flows_revenue": kp.get("flows_revenue"),
                "campaigns": kp.get("campaigns") or [],
                "flows": kp.get("flows") or [],
                "error": kp.get("error"),   # eventuale errore SOLO-flows (campagne ok)
            }
        except Exception as exc:  # noqa: BLE001 — fallimento reale -> fallback DB + warning
            kla[month] = {"loaded": False, "error": str(exc)}
        placeholder.empty()

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
