"""
Test deterministici delle metriche Meta (nessuna rete, nessuna credenziale).
Verificano conversione valuta EUR->USD ed estrazione acquisti/ROAS/CPA/CVR.
"""
from config import settings
from src.metrics.meta import compute_meta_metrics


def _row(cid, name, spend, clicks, purchases, revenue, ptype="omni_purchase"):
    return {
        "campaign_id": cid,
        "campaign_name": name,
        "spend": spend,
        "clicks": clicks,
        "impressions": "1000",
        "actions": [{"action_type": ptype, "value": purchases}],
        "action_values": [{"action_type": ptype, "value": revenue}],
    }


def test_meta_eur_conversion_and_ratios():
    fx = settings.EUR_TO_USD  # default 1.08
    rows = [_row("c1", "Camp A", "10", "100", "2", "50")]
    m = compute_meta_metrics("2026-05-29", rows, account_currency="EUR")

    assert m.account_currency == "EUR"
    assert round(m.spend, 4) == round(10 * fx, 4)        # 10 EUR -> USD
    assert round(m.revenue, 4) == round(50 * fx, 4)      # 50 EUR -> USD
    assert m.orders == 2
    # ROAS = revenue/spend (il fx si semplifica) = 50/10 = 5.0
    assert round(m.roas, 4) == 5.0
    # CPA = spend/orders = (10*fx)/2
    assert round(m.cpa, 4) == round((10 * fx) / 2, 4)
    c = m.campaigns[0]
    assert round(c.cvr, 4) == 0.02  # 2 ordini / 100 click


def test_meta_usd_no_conversion_and_totals():
    rows = [
        _row("c1", "A", "100", "200", "5", "400"),
        _row("c2", "B", "50", "100", "0", "0"),
    ]
    m = compute_meta_metrics("2026-05-29", rows, account_currency="USD")
    assert m.fx_to_usd == 1.0
    assert round(m.spend, 2) == 150.0
    assert round(m.revenue, 2) == 400.0
    assert m.orders == 5
    assert round(m.roas, 4) == round(400 / 150, 4)
    # campagna senza acquisti: CPA e ROAS = 0 (niente divisioni per zero)
    b = next(c for c in m.campaigns if c.campaign_id == "c2")
    assert b.roas == 0.0 and b.cpa == 0.0
