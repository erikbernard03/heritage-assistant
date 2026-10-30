"""
Regressione: la revenue ESCLUDE le tasse su entrambi gli store (heritagering.com e .co).
Copre: ordine con IVA, rimborso PARZIALE che include IVA (netting via current_total_tax),
ordine .co in presentment EUR con fx. revenue = (prezzo − tassa) in USD. Le tasse non sono
mai nostre (dovute allo Stato): mai in revenue né in net profit.
"""
from config import settings
from src.config_loader import CogsResolver
from src.metrics.profit import (
    compute_daily_metrics,
    order_revenue_usd,
    order_tax_usd,
)
from src.metrics.sales_location import revenue_by_country

RESOLVER = CogsResolver()


def test_com_order_with_tax_and_partial_refund_nets_current_tax():
    """.com (USD): rimborso parziale che include IVA. revenue usa current_* (netto rimborsi),
    e la tassa sottratta è current_total_tax (20), NON il total_tax lordo (21)."""
    o = {
        "id": 1,
        # lordo: 121 (100 prodotto + 21 IVA). Rimborso parziale -> current 110, tassa attuale 20.
        "total_price": "121.00", "total_tax": "21.00",
        "current_total_price_set": {"shop_money": {"amount": "110.00"}},
        "current_total_tax_set": {"shop_money": {"amount": "20.00"}},
        "current_total_price": "110.00", "current_total_tax": "20.00",
    }
    # revenue = 110 − 20 = 90 (NON 110 − 21 = 89: usiamo current_total_tax, non total_tax lordo)
    assert round(order_revenue_usd(o, 1.0), 2) == 90.00
    assert round(order_tax_usd(o, 1.0), 2) == 20.00


def test_co_order_eur_presentment_with_vat_converted_to_usd():
    """.co: shop_money in EUR (valuta base store), IVA 22%, fx EUR->USD 1.08.
    revenue = (100 − 22) × 1.08 = 84.24 USD; tassa = 22 × 1.08 = 23.76 USD."""
    o = {
        "id": 2,
        "current_total_price_set": {
            "shop_money": {"amount": "100.00", "currency_code": "EUR"},
            "presentment_money": {"amount": "100.00", "currency_code": "EUR"},
        },
        "current_total_tax_set": {
            "shop_money": {"amount": "22.00", "currency_code": "EUR"},
            "presentment_money": {"amount": "22.00", "currency_code": "EUR"},
        },
    }
    assert round(order_revenue_usd(o, 1.08), 2) == 84.24
    assert round(order_tax_usd(o, 1.08), 2) == 23.76


def test_missing_tax_field_treated_as_zero():
    """Ordine senza alcun campo tassa (es. US senza sales tax): tassa = 0, revenue invariata."""
    o = {"id": 3, "current_total_price": "131.00"}
    assert round(order_revenue_usd(o, 1.0), 2) == 131.00
    assert order_tax_usd(o, 1.0) == 0.0


def test_daily_metrics_revenue_excludes_tax_both_stores():
    """compute_daily_metrics: revenue combinata esclude le tasse; tax_collected le traccia a parte."""
    com = {"id": 1, "current_total_price": "60.00", "current_total_tax": "6.00", "line_items": []}
    m_com = compute_daily_metrics("2026-06-01", [com], {}, resolver=RESOLVER, currency_to_usd=1.0)
    assert round(m_com.revenue, 2) == 54.00          # 60 − 6
    assert round(m_com.tax_collected, 2) == 6.00

    co = {"id": 2,
          "current_total_price_set": {"shop_money": {"amount": "100.00"}},
          "current_total_tax_set": {"shop_money": {"amount": "22.00"}},
          "line_items": []}
    m_co = compute_daily_metrics("2026-06-01", [co], {}, resolver=RESOLVER,
                                 fee_rate=settings.PAYMENT_FEE_RATE_CO, currency_to_usd=1.08)
    assert round(m_co.revenue, 2) == 84.24           # (100 − 22) × 1.08
    assert round(m_co.tax_collected, 2) == 23.76     # 22 × 1.08


def test_breakdowns_exclude_tax_and_apply_fx():
    """I breakdown (country/hour/source) passano dallo stesso helper: netto tasse + fx."""
    co_order = {
        "id": 9,
        "current_total_price_set": {"shop_money": {"amount": "100.00"}},
        "current_total_tax_set": {"shop_money": {"amount": "22.00"}},
        "shipping_address": {"country_code": "IT"},
    }
    by_country = revenue_by_country([co_order], currency_to_usd=1.08)
    assert round(by_country["IT"]["revenue"], 2) == 84.24   # (100 − 22) × 1.08, tasse escluse
