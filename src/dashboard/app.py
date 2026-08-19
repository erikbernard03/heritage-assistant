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
from datetime import timedelta

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
    }


# --------------------------------------------------------------------------- #
# Helpers di formato
# --------------------------------------------------------------------------- #
def _usd(x) -> str:
    return f"${float(x or 0):,.2f}"


def _pct(frac) -> str:
    return f"{float(frac or 0) * 100:.2f}%"


def _cvr(frac) -> str:
    """Store CVR con la stessa guardia di sanity dei report (>10% -> 'n/a')."""
    from src.report import CVR_SANITY_MAX

    v = float(frac or 0)
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


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main() -> None:
    if not _check_password():
        st.stop()

    st.title("📊 Heritage Ring")
    label, start_iso, end_iso = _select_period()
    st.caption(f"**{label}** · {start_iso} → {end_iso} · USD · read-only (nightly data)")

    try:
        data = _compute_period(start_iso, end_iso)
    except Exception as exc:  # noqa: BLE001
        st.error(f"Could not load data: {exc}")
        st.stop()

    if not data:
        st.info("No data for the selected period yet.")
        st.stop()

    m = data["metrics"]
    _render_kpis(m, data["breakeven"])
    _render_trend(data["daily_rows"])
    _render_platforms(data)
    _render_meta_campaigns(data["meta_campaigns"])
    _render_cost_breakdown(m, data["num_days"])

    if st.button("🔄 Refresh data"):
        st.cache_data.clear()
        st.rerun()


# Streamlit esegue lo script top-level ad ogni interazione.
main()
