"""
Test deterministici dell'estrazione/calcolo Google Ads (no rete, no credenziali).

Verificano la mappatura per metricId (values.current), valori in USD (no conversione),
e l'uso del ROAS/CPA riportati.
"""
from src.connectors.triplewhale import extract_google
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
    c = compute_google_metrics("2026-05-31", g)
    assert c.account_currency == "USD"
    assert c.fx_to_usd == 1.0
    assert round(c.spend, 2) == 210.50
    assert c.roas == 3.2
    assert c.cpa == 18.0
    assert c.orders == 12
