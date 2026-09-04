"""
Test deterministici della classificazione LAST-CLICK per sorgente e delle aggregazioni.
Più il parsing dei tile pixel Triple Whale. Nessuna rete.
"""
from src.connectors.triplewhale import extract_pixel_attribution
from src.metrics.sales_source import (
    aggregate_sources,
    classify_order,
    group_of,
    revenue_by_source,
    rollup_groups,
    sales_by_source_by_month,
    top_sources,
    tw_pixel_by_month,
)


def _order(landing=None, referring=None, notes=None, total="100.00", cancelled=None):
    o = {"total_price": total}
    if landing is not None:
        o["landing_site"] = landing
    if referring is not None:
        o["referring_site"] = referring
    if notes is not None:
        o["note_attributes"] = notes
    if cancelled:
        o["cancelled_at"] = cancelled
    return o


# --------------------------------------------------------------------------- META
def test_meta_from_utm_source_variants():
    for s in ("facebook", "instagram", "fb", "ig", "Facebook"):
        assert classify_order(_order(landing=f"/p?utm_source={s}&utm_medium=paid")) == "meta"


def test_meta_from_referrer():
    assert classify_order(_order(referring="https://l.facebook.com/")) == "meta"
    assert classify_order(_order(referring="https://www.instagram.com/")) == "meta"


# ------------------------------------------------------------------------- GOOGLE
def test_google_paid_vs_organic():
    assert classify_order(_order(landing="/p?utm_source=google&utm_medium=cpc")) == "google_paid"
    assert classify_order(_order(landing="/p?utm_source=google&utm_medium=ppc")) == "google_paid"
    # referrer google senza UTM a pagamento -> organico
    assert classify_order(_order(referring="https://www.google.com/")) == "google_organic"
    assert classify_order(_order(landing="/p?utm_source=google&utm_medium=organic")) == "google_organic"


# -------------------------------------------------------------------- EMAIL / ALTRO
def test_email_klaviyo():
    assert classify_order(_order(landing="/p?utm_source=klaviyo&utm_medium=email")) == "email"
    assert classify_order(_order(landing="/p?utm_source=whatever&utm_medium=email")) == "email"


def test_tiktok_and_pinterest():
    assert classify_order(_order(landing="/p?utm_source=tiktok")) == "tiktok"
    assert classify_order(_order(referring="https://www.tiktok.com/")) == "tiktok"
    assert classify_order(_order(landing="/p?utm_source=pinterest")) == "pinterest"
    assert classify_order(_order(referring="https://pinterest.com/")) == "pinterest"


def test_direct_and_other():
    # nessun referrer, nessun UTM -> direct
    assert classify_order(_order(landing="/products/foo")) == "direct"
    assert classify_order(_order()) == "direct"
    # sconosciuto -> conserva la stringa grezza della sorgente
    assert classify_order(_order(landing="/p?utm_source=reddit&utm_medium=social")) == "reddit"
    assert classify_order(_order(referring="https://news.ycombinator.com/")) == "news.ycombinator.com"


def test_email_from_kx_param_without_utm():
    # Klaviyo appende _kx anche senza UTM: dev'essere classificato email (A).
    assert classify_order(_order(landing="/products/foo?_kx=abc123.XyZ")) == "email"
    assert classify_order(_order(landing="/p?klclid=deadbeef")) == "email"
    # senza _kx e senza UTM resta direct
    assert classify_order(_order(landing="/products/foo")) == "direct"


def test_internal_referrer_never_a_source():
    # Referrer interno (pagina ring-sizer di heritagering.com) -> NON è una sorgente (B).
    assert classify_order(_order(referring="https://heritagering.com/apps/ring-sizer")) == "direct"
    assert classify_order(_order(referring="https://www.heritagering.com/pages/sizer")) == "direct"
    assert classify_order(_order(referring="https://heritagering.myshopify.com/")) == "direct"
    # referrer interno MA con UTM valido -> vince l'UTM
    assert classify_order(_order(landing="/p?utm_source=facebook",
                                 referring="https://heritagering.com/apps/ring-sizer")) == "meta"


def test_rollup_groups_paid_organic_email():
    by = {
        "meta": {"orders": 10, "revenue": 1000.0},
        "google_paid": {"orders": 5, "revenue": 500.0},
        "google_organic": {"orders": 2, "revenue": 200.0},
        "direct": {"orders": 3, "revenue": 100.0},
        "email": {"orders": 4, "revenue": 400.0},
        "reddit": {"orders": 1, "revenue": 50.0},          # sconosciuto -> Organic
    }
    roll = {r["group"]: r for r in rollup_groups(by)}
    assert roll["Paid"]["revenue"] == 1500.0 and roll["Paid"]["orders"] == 15
    assert roll["Email"]["revenue"] == 400.0
    assert roll["Organic"]["revenue"] == 350.0            # 200 + 100 + 50 (reddit)
    assert group_of("reddit") == "Organic" and group_of("meta") == "Paid"
    # % sul totale (2250)
    assert roll["Paid"]["pct"] == round(1500 / 2250 * 100, 1)


def test_utm_from_note_attributes_fallback():
    o = _order(notes=[{"name": "utm_source", "value": "facebook"},
                      {"name": "utm_medium", "value": "paid"}])
    assert classify_order(o) == "meta"


# ---------------------------------------------------------------- aggregazioni
def test_revenue_by_source_excludes_cancelled():
    orders = [
        _order(landing="/p?utm_source=facebook", total="120.00"),
        _order(landing="/p?utm_source=facebook", total="80.00"),
        _order(landing="/p?utm_source=klaviyo&utm_medium=email", total="50.00"),
        _order(landing="/p?utm_source=facebook", total="999.00", cancelled="2026-09-02"),
    ]
    by = revenue_by_source(orders)
    assert by["meta"] == {"revenue": 200.0, "orders": 2}      # cancellato escluso
    assert by["email"] == {"revenue": 50.0, "orders": 1}


def test_aggregate_sources_and_top_and_pct():
    by = {"meta": {"orders": 2, "revenue": 200.0}, "email": {"orders": 1, "revenue": 50.0}}
    agg = aggregate_sources(by)
    assert agg[0]["source"] == "meta" and agg[0]["pct"] == 80.0
    assert agg[1]["source"] == "email" and agg[1]["pct"] == 20.0
    assert [t["source"] for t in top_sources(by, 1)] == ["meta"]


def test_sales_by_source_by_month():
    rows = [
        {"day": "2026-08-31", "source": "meta", "orders": 3, "revenue": 300.0},
        {"day": "2026-09-01", "source": "meta", "orders": 2, "revenue": 200.0},
        {"day": "2026-09-02", "source": "email", "orders": 1, "revenue": 40.0},
    ]
    by = sales_by_source_by_month(rows)
    assert by["2026-09"]["meta"] == {"orders": 2, "revenue": 200.0}
    assert by["2026-09"]["email"] == {"orders": 1, "revenue": 40.0}
    assert by["2026-08"]["meta"] == {"orders": 3, "revenue": 300.0}


# ------------------------------------------------------------- Triple Whale pixel
def test_extract_platform_reported_ids_and_kind():
    # ids CONFERMATI dalla discovery /tw_check (platform-reported via TW).
    summary = [
        {"metricId": "facebookPurchases", "values": {"current": 42}},
        {"metricId": "facebookConversionValue", "values": {"current": 5000.0}},
        {"metricId": "ga_all_transactions_adGroup", "values": {"current": 8}},
        {"metricId": "ga_all_transactionsRevenue_adGroup", "values": {"current": 900.0}},
        {"metricId": "tiktokPurchases", "values": {"current": 5}},
        {"metricId": "tiktokConversionValue", "values": {"current": 300.0}},
        {"metricId": "pixelPurchases", "values": {"current": 120}},
        {"metricId": "pixelConversionValue", "values": {"current": 14000.0}},
    ]
    px = extract_pixel_attribution(summary)
    assert px["meta"]["orders"] == 42.0 and px["meta"]["revenue"] == 5000.0
    assert px["meta"]["kind"] == "platform-reported"
    assert px["meta"]["orders_metric"] == "facebookPurchases"
    assert px["google"]["revenue"] == 900.0 and px["google"]["kind"] == "platform-reported"
    assert px["tiktok"]["orders"] == 5.0
    # totale pixel del negozio
    assert px["pixel_total"] == {"orders": 120.0, "revenue": 14000.0, "kind": "pixel",
                                 "orders_metric": "pixelPurchases",
                                 "revenue_metric": "pixelConversionValue"}


def test_extract_prefers_per_channel_pixel_over_platform():
    # Se esiste il pixel per-canale, va preferito e marcato kind=pixel.
    summary = [
        {"metricId": "facebookPurchases", "values": {"current": 42}},          # platform
        {"metricId": "pixelFacebookPurchases", "values": {"current": 30}},      # pixel (preferito)
        {"metricId": "pixelFacebookConversionValue", "values": {"current": 3300.0}},
    ]
    px = extract_pixel_attribution(summary)
    assert px["meta"]["orders"] == 30.0 and px["meta"]["kind"] == "pixel"
    assert px["meta"]["orders_metric"] == "pixelFacebookPurchases"


def test_tw_pixel_by_month():
    rows = [
        {"day": "2026-09-01", "channel": "meta", "orders": 40, "revenue": 4000.0,
         "kind": "platform-reported"},
        {"day": "2026-09-02", "channel": "meta", "orders": 2, "revenue": 200.0,
         "kind": "platform-reported"},
        {"day": "2026-09-02", "channel": "google", "orders": 3, "revenue": 300.0,
         "kind": "platform-reported"},
    ]
    by = tw_pixel_by_month(rows)
    assert by["2026-09"]["meta"]["orders"] == 42.0 and by["2026-09"]["meta"]["revenue"] == 4200.0
    assert by["2026-09"]["meta"]["kind"] == "platform-reported"
    assert by["2026-09"]["google"]["orders"] == 3.0 and by["2026-09"]["google"]["revenue"] == 300.0
