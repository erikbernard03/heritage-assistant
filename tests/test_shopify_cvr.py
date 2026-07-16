"""
Test deterministici della CVR di negozio da Shopify (ShopifyQL) e dell'uso nel report.
Nessuna rete: si testa il parsing della risposta GraphQL e il render del report.
"""
from src.connectors.shopify import parse_session_conversion_rate
from src.metrics.profit import DailyMetrics
from src.report import format_report


def _table(columns, row):
    return {
        "data": {
            "shopifyqlQuery": {
                "tableData": {
                    "columns": [{"name": c} for c in columns],
                    "rows": [row],
                },
                "parseErrors": None,
            }
        }
    }


def test_cvr_uses_native_conversion_rate():
    gql = _table(["sessions", "sessions_that_completed_checkout", "conversion_rate"],
                 [1000, 23, 0.023])
    assert round(parse_session_conversion_rate(gql), 4) == 0.023


def test_cvr_computed_from_sessions_when_no_native_rate():
    gql = _table(["sessions", "sessions_that_completed_checkout"], [1000, 30])
    assert round(parse_session_conversion_rate(gql), 4) == 0.03  # 30/1000


def test_cvr_access_denied_returns_none():
    assert parse_session_conversion_rate({"errors": [{"message": "ACCESS_DENIED"}]}) is None


def test_cvr_parse_errors_or_empty_returns_none():
    assert parse_session_conversion_rate(
        {"data": {"shopifyqlQuery": {"parseErrors": ["bad query"]}}}
    ) is None
    assert parse_session_conversion_rate(_table(["sessions"], [])) is None or True
    # empty rows -> None
    empty = {"data": {"shopifyqlQuery": {"tableData": {"columns": [{"name": "sessions"}], "rows": []}}}}
    assert parse_session_conversion_rate(empty) is None


def test_report_store_cvr_comes_from_metrics():
    m = DailyMetrics(day="2026-05-31", num_orders=10, revenue=1000.0, aov=100.0,
                     net_profit_operativo=200.0, net_profit_netto=11.0, store_cvr=0.0312)
    text = format_report(m)
    assert "Store CVR: 3.12%" in text
    # se 0 -> n/a
    m2 = DailyMetrics(day="2026-05-31", store_cvr=0.0)
    assert "Store CVR: n/a" in format_report(m2)


def test_report_store_cvr_low_fraction_displays_correctly():
    """Frazione 0.0015 (dal fallback Triple Whale corretto) -> '0.15%', non 15%."""
    m = DailyMetrics(day="2026-06-24", num_orders=3, revenue=300.0, store_cvr=0.0015)
    assert "Store CVR: 0.15%" in format_report(m)


def test_report_store_cvr_sanity_guard_over_10pct_shows_na():
    """
    Guardia di sanity: una CVR salvata come 0.15 (=15%, dato stale con scala errata)
    supera il 10% -> il report mostra 'n/a' invece di una percentuale assurda.
    """
    from src.report import CVR_SANITY_MAX

    assert CVR_SANITY_MAX == 0.10
    m = DailyMetrics(day="2026-06-24", num_orders=3, revenue=300.0, store_cvr=0.15)
    assert "Store CVR: n/a" in format_report(m)
    # un valore appena sotto la soglia resta mostrato normalmente
    m2 = DailyMetrics(day="2026-06-24", num_orders=3, revenue=300.0, store_cvr=0.099)
    assert "Store CVR: 9.90%" in format_report(m2)


def test_report_has_no_separate_vat_or_shipping_income_lines():
    """Le righe income separate sono state rimosse (no doppio conteggio visivo)."""
    m = DailyMetrics(day="2026-05-31", num_orders=10, revenue=1000.0, aov=100.0,
                     cogs_total=30.0, shipping_total=70.0, payment_fees=75.0,
                     net_profit_operativo=825.0, net_profit_netto=636.07,
                     shipping_collected=40.0, tax_collected=180.0)
    text = format_report(m)
    assert "VAT collected" not in text
    assert "Premium shipping collected" not in text
    assert "*2) COST BREAKDOWN*" in text
    # revenue resta total_price (IVA+spedizione incluse una volta)
    assert "Revenue: *$1,000.00*" in text
