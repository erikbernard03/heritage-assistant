"""
Test deterministici dell'estrazione/calcolo TikTok (no rete, no credenziali).

Verificano la mappatura per metricId (values.current), spend totale (tracked +
GMV Max), revenue = spend × ROAS, e che i valori siano in USD (no conversione).
"""
from src.connectors.triplewhale import extract_tiktok
from src.metrics.tiktok import compute_tiktok_metrics


def _summary_with_tiktok():
    return {
        "data": [
            {"metricId": "facebook_spend", "values": {"current": 500}},
            {"metricId": "tiktok_spend", "title": "TikTok Ad Spend",
             "values": {"current": 145.94}},
            {"metricId": "tiktokNonTrackedSpend", "values": {"current": 20.0}},
            {"metricId": "tiktok_complete_payment_roas", "values": {"current": 2.1}},
            {"metricId": "tiktokImpressions", "values": {"current": 10000}},
            {"metricId": "tiktok_clicks", "values": {"current": 300}},
            {"metricId": "averageTiktokCpm", "values": {"current": 14.6}},
            {"metricId": "tiktokPurchases", "values": {"current": 2}},
            {"metricId": "tiktokCpa", "values": {"current": 72.97}},
            {"metricId": "tiktokConversionValue", "values": {"current": 307.0}},
        ]
    }


def test_extract_tiktok_from_metric_tiles():
    tk = extract_tiktok(_summary_with_tiktok())
    assert tk is not None
    assert round(tk["tracked_spend"], 2) == 145.94
    assert tk["non_tracked_spend"] == 20.0
    assert round(tk["spend"], 2) == 165.94                 # tracked + GMV Max
    assert tk["roas"] == 2.1
    assert tk["revenue"] == 307.0       # tiktokConversionValue (reale, non spend×roas)
    assert tk["orders"] == 2            # tiktokPurchases
    assert tk["cpa"] == 72.97           # tiktokCpa (riportato)
    assert tk["impressions"] == 10000
    assert tk["clicks"] == 300
    assert tk["cpm"] == 14.6
    assert tk["currency"] == "USD"
    assert tk["campaigns"] == []


def test_extract_tiktok_absent_returns_none():
    summary = {"data": [{"metricId": "facebook_spend", "values": {"current": 500}}]}
    assert extract_tiktok(summary) is None


def test_compute_from_extracted_tiktok():
    tk = extract_tiktok(_summary_with_tiktok())
    c = compute_tiktok_metrics("2026-05-31", tk)
    assert c.account_currency == "USD"
    assert c.fx_to_usd == 1.0
    assert round(c.spend, 2) == 165.94    # totale, usato nel net profit
    assert c.roas == 2.1
    assert c.revenue == 307.0
    assert c.orders == 2
    assert c.cpa == 72.97                  # CPA riportato (non spend/orders)
    assert c.campaigns == []
