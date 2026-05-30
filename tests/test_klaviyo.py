"""
Test deterministici delle metriche Klaviyo (no rete, no credenziali).

Verificano l'aggregazione campagne + totali (SOLO campagne) e i tassi.
"""
from src.metrics.klaviyo import compute_klaviyo_metrics


def _result(campaign_id, conversion_value, opens, clicks, conversions, recipients):
    return {
        "groupings": {"campaign_id": campaign_id},
        "statistics": {
            "conversion_value": conversion_value,
            "opens": opens,
            "clicks": clicks,
            "conversions": conversions,
            "recipients": recipients,
        },
    }


def test_klaviyo_aggregation_and_rates():
    raw = [
        _result("c1", 200.0, 50, 10, 4, 100),
        _result("c2", 50.0, 20, 5, 1, 100),
    ]
    names = {"c1": "May Newsletter", "c2": "Flash Sale"}
    k = compute_klaviyo_metrics("2026-05-29", raw, names=names)

    # totali
    assert round(k.revenue, 2) == 250.0
    assert k.opens == 70
    assert k.clicks == 15
    assert k.conversions == 5
    assert k.recipients == 200
    # tassi giornalieri ricalcolati sul totale
    assert round(k.open_rate, 4) == 0.35      # 70/200
    assert round(k.click_rate, 4) == 0.075    # 15/200

    # ordinamento per revenue desc + nomi risolti
    assert k.campaigns[0].campaign_id == "c1"
    assert k.campaigns[0].campaign_name == "May Newsletter"
    assert round(k.campaigns[0].open_rate, 2) == 0.50   # 50/100


def test_klaviyo_empty():
    k = compute_klaviyo_metrics("2026-05-29", [])
    assert k.revenue == 0.0
    assert k.campaigns == []
    assert k.open_rate == 0.0
