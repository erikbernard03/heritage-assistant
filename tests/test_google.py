"""
Test deterministici dell'estrazione/calcolo Google Ads (no rete, no credenziali).

Verificano la mappatura per metricId (values.current), valori in USD (no conversione),
e l'uso del ROAS/CPA riportati.
"""
from src.connectors.triplewhale import extract_google, extract_store_cvr
from src.metrics.google import compute_google_metrics


def _summary_with_google():
    return {
        "data": [
            {"metricId": "tiktok_spend", "values": {"current": 145.94}},
            {"metricId": "ga_adCost", "values": {"current": 210.50}},
            {"metricId": "ga_ROAS", "values": {"current": 3.2}},
            {"metricId": "googleCpa", "values": {"current": 18.0}},
            {"metricId": "totalGoogleAdsClicks", "values": {"current": 640}},
            {"metricId": "totalGoogleAdsImpressions", "values": {"current": 51000}},
            {"metricId": "ga_all_transactions_adGroup", "values": {"current": 12}},
            {"metricId": "ga_all_transactionsRevenue_adGroup", "values": {"current": 673.6}},
        ]
    }


def test_extract_google_from_metric_tiles():
    g = extract_google(_summary_with_google())
    assert g is not None
    assert round(g["spend"], 2) == 210.50
    assert g["roas"] == 3.2
    assert g["cpa"] == 18.0
    assert g["clicks"] == 640
    assert g["impressions"] == 51000
    assert g["orders"] == 12
    assert round(g["revenue"], 2) == 673.6
    assert g["currency"] == "USD"


def test_extract_google_cpa_fallback_to_googleAllCpa():
    summary = {"data": [
        {"metricId": "ga_adCost", "values": {"current": 100.0}},
        {"metricId": "googleAllCpa", "values": {"current": 25.0}},
    ]}
    g = extract_google(summary)
    assert g["cpa"] == 25.0  # usa googleAllCpa quando googleCpa assente


def test_extract_google_absent_returns_none():
    summary = {"data": [{"metricId": "tiktok_spend", "values": {"current": 50}}]}
    assert extract_google(summary) is None


def test_compute_google_metrics_usd():
    g = extract_google(_summary_with_google())
    c = compute_google_metrics("2026-05-31", g, store_cvr=0.0234)
    assert c.account_currency == "USD"
    assert c.fx_to_usd == 1.0
    assert round(c.spend, 2) == 210.50
    assert c.roas == 3.2
    assert c.cpa == 18.0
    assert c.orders == 12
    assert round(c.store_cvr, 4) == 0.0234
    assert c.as_db_row()["store_cvr"] == 0.0234


def test_extract_store_cvr_from_sessions():
    # preferito: pixelPurchases / sessions -> 10/400 = 0.025 = 2.5%
    summary = {"data": [
        {"metricId": "pixelPurchases", "values": {"current": 10}},
        {"metricId": "sessions", "values": {"current": 400}},
        {"metricId": "pixelConversionRate", "values": {"current": 0.4399}},
    ]}
    assert round(extract_store_cvr(summary), 4) == 0.025


def test_extract_store_cvr_fallback_pixelConversionRate_as_percent():
    # nessuna sessione -> pixelConversionRate (0.4399 inteso come 0.4399%)
    summary = {"data": [
        {"metricId": "pixelPurchases", "values": {"current": 10}},
        {"metricId": "pixelConversionRate", "values": {"current": 0.4399}},
    ]}
    cvr = extract_store_cvr(summary)
    assert round(cvr * 100, 4) == 0.4399   # display = 0.44%


def test_extract_store_cvr_low_pct_is_scaled_not_treated_as_fraction():
    """
    REGRESSIONE (bug ×100): pixelConversionRate = 0.15 significa 0.15%, NON 15%.
    La vecchia euristica '>0.2 => già %' NON convertiva 0.15 e lo mostrava come 15%.
    Ora è sempre /100: 0.15 -> frazione 0.0015 -> display 0.15%.
    """
    summary = {"data": [
        {"metricId": "pixelConversionRate", "values": {"current": 0.15}},
    ]}
    cvr = extract_store_cvr(summary)
    assert round(cvr, 6) == 0.0015          # frazione, NON 0.15
    assert round(cvr * 100, 2) == 0.15      # display = 0.15%


def test_extract_store_cvr_fallback_scale_endtoend_stores_fraction():
    """Il valore che finisce in google_daily.store_cvr è una FRAZIONE (0.0015), non 0.15."""
    summary = {"data": [
        {"metricId": "pixelConversionRate", "values": {"current": 0.15}},
    ]}
    cvr = extract_store_cvr(summary)
    c = compute_google_metrics("2026-06-24", {}, store_cvr=cvr or 0.0)
    assert round(c.store_cvr, 6) == 0.0015
    assert c.as_db_row()["store_cvr"] == 0.0015


def test_extract_store_cvr_typical_percent_values_scaled():
    # 2.5 (=2.5%) -> 0.025 ; 0.44 (=0.44%) -> 0.0044
    for raw, frac in ((2.5, 0.025), (0.44, 0.0044)):
        summary = {"data": [{"metricId": "pixelConversionRate", "values": {"current": raw}}]}
        assert round(extract_store_cvr(summary), 6) == frac


def test_extract_store_cvr_absent_returns_none():
    assert extract_store_cvr({"data": [{"metricId": "ga_adCost", "values": {"current": 10}}]}) is None
