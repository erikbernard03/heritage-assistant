"""
Test del parsing degli scope concessi al token Shopify (get_granted_scopes).
Nessuna rete: monkeypatch del fetch token / risposta grant.
"""
from src.connectors.shopify import ShopifyConnector


def _conn():
    return ShopifyConnector(store="x.myshopify.com", client_id="cid", client_secret="sec")


def test_granted_scopes_parsed_from_grant_scope_field(monkeypatch):
    conn = _conn()

    def _fake_fetch():
        conn._access_token = "tok"
        conn._token_expiry = 9e18
        conn._granted_scopes = [
            s.strip() for s in
            "read_orders, read_all_orders ,read_products,read_reports".split(",")
            if s.strip()
        ]
        return "tok"

    monkeypatch.setattr(conn, "_fetch_access_token", _fake_fetch)
    scopes = conn.get_granted_scopes()
    assert scopes == ["read_orders", "read_all_orders", "read_products", "read_reports"]
    assert "read_all_orders" in scopes and "read_reports" in scopes


def test_granted_scopes_empty_when_grant_has_none(monkeypatch):
    conn = _conn()

    def _fake_fetch():
        conn._access_token = "tok"
        conn._token_expiry = 9e18
        conn._granted_scopes = []           # nessuno scope nel grant
        return "tok"

    # e l'endpoint di fallback fallisce -> lista vuota, nessuna eccezione
    monkeypatch.setattr(conn, "_fetch_access_token", _fake_fetch)
    monkeypatch.setattr(conn, "_request",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("denied")))
    assert conn.get_granted_scopes() == []
