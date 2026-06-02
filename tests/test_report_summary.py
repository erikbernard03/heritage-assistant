"""
Test: un SINGOLO Summary Triple Whale alimenta TikTok, Google e Store CVR.

Replica il bug per cui Google/CVR risultavano vuoti mentre TikTok funzionava:
ora il report passa lo stesso oggetto `summary` a tutti e tre.
"""
from src.connectors.triplewhale import extract_google, extract_store_cvr, extract_tiktok
from src.report import _load_google, _load_tiktok


def _combined_summary():
    return {
        "data": [
            # --- TikTok ---
            {"metricId": "tiktok_spend", "values": {"current": 145.94}},
            {"metricId": "tiktokNonTrackedSpend", "values": {"current": 0}},
            {"metricId": "tiktok_complete_payment_roas", "values": {"current": 2.1}},
            {"metricId": "tiktokImpressions", "values": {"current": 42000}},
            {"metricId": "tiktok_clicks", "values": {"current": 510}},
            {"metricId": "averageTiktokCpm", "values": {"current": 3.48}},
            {"metricId": "tiktokPurchases", "values": {"current": 2}},
            {"metricId": "tiktokCpa", "values": {"current": 72.97}},
            {"metricId": "tiktokConversionValue", "values": {"current": 307.0}},
            # --- Google ---
            {"metricId": "ga_adCost", "values": {"current": 181.89}},
            {"metricId": "ga_ROAS", "values": {"current": 3.2}},
            {"metricId": "googleCpa", "values": {"current": 0}},
            {"metricId": "totalGoogleAdsClicks", "values": {"current": 640}},
            {"metricId": "totalGoogleAdsImpressions", "values": {"current": 51000}},
            {"metricId": "ga_all_transactions_adGroup", "values": {"current": 24}},
            {"metricId": "ga_all_transactionsRevenue_adGroup", "values": {"current": 3498.62}},
            # --- Store CVR ---
            {"metricId": "pixelConversionRate", "values": {"current": 0.4399}},
            {"metricId": "pixelPurchases", "values": {"current": 10}},
        ]
    }


def test_one_summary_feeds_tiktok_google_cvr():
    s = _combined_summary()
    tk = extract_tiktok(s)
    g = extract_google(s)
    cvr = extract_store_cvr(s)

    assert tk is not None
    assert round(tk["spend"], 2) == 145.94 and tk["orders"] == 2 and tk["cpa"] == 72.97
    assert g is not None
    assert round(g["spend"], 2) == 181.89 and g["orders"] == 24
    assert round(g["revenue"], 2) == 3498.62
    assert cvr is not None and round(cvr * 100, 4) == 0.4399  # 0.44%


def test_loaders_populate_from_same_summary_object():
    """_load_tiktok e _load_google ricevono lo STESSO dict e popolano entrambi."""
    s = _combined_summary()
    tt_daily, _camps, tt_spend = _load_tiktok("2026-05-31", s, persist=False)
    g_daily, g_spend = _load_google("2026-05-31", s, persist=False)

    assert tt_daily is not None and round(tt_spend, 2) == 145.94
    assert g_daily is not None and round(g_spend, 2) == 181.89
    # CVR salvata in google_daily, dallo stesso summary
    assert round(float(g_daily["store_cvr"]) * 100, 4) == 0.4399


def test_loaders_none_summary_degrade():
    assert _load_tiktok("2026-05-31", None, persist=False) == (None, [], 0.0)
    assert _load_google("2026-05-31", None, persist=False) == (None, 0.0)
