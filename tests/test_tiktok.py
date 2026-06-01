"""
Test deterministici delle metriche TikTok (no rete, no credenziali).

Verificano conversione EUR->USD, ROAS/CPA e il breakdown campagne.
"""
from config import settings
from src.metrics.tiktok import compute_tiktok_metrics


def test_tiktok_usd_passthrough_and_ratios():
    tiktok = {
        "currency": "USD",
        "spend": 100.0,
        "revenue": 250.0,
        "orders": 5,
        "clicks": 50,
        "impressions": 1000,
        "roas": 0,  # non riportato -> ricalcolato revenue/spend
        "campaigns": [],
    }
    t = compute_tiktok_metrics("2026-05-31", tiktok)
    assert t.account_currency == "USD"
    assert t.fx_to_usd == 1.0
    assert t.spend == 100.0
    assert round(t.roas, 4) == 2.5      # 250/100
    assert round(t.cpa, 2) == 20.0      # 100/5


def test_tiktok_eur_converted_to_usd():
    eur_usd = settings.EUR_TO_USD
    tiktok = {"currency": "EUR", "spend": 80.0, "revenue": 160.0, "orders": 4,
              "clicks": 40, "impressions": 800, "campaigns": [
                  {"campaign_id": "c1", "campaign_name": "TT Spark", "spend": 80.0,
                   "revenue": 160.0, "orders": 4, "clicks": 40, "impressions": 800}]}
    t = compute_tiktok_metrics("2026-05-31", tiktok)
    assert t.account_currency == "EUR"
    assert round(t.fx_to_usd, 6) == round(eur_usd, 6)
    # spesa convertita in USD
    assert round(t.spend, 2) == round(80.0 * eur_usd, 2)
    assert round(t.revenue, 2) == round(160.0 * eur_usd, 2)
    # ROAS invariato dalla conversione (160/80 = 2.0)
    assert round(t.roas, 4) == 2.0
    # campagna convertita anch'essa
    assert t.campaigns[0].campaign_name == "TT Spark"
    assert round(t.campaigns[0].spend, 2) == round(80.0 * eur_usd, 2)
    assert round(t.campaigns[0].cvr, 4) == 0.1  # 4/40


def test_tiktok_empty_node():
    t = compute_tiktok_metrics("2026-05-31", {"currency": "USD"})
    assert t.spend == 0.0
    assert t.campaigns == []
