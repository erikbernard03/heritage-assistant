"""
Test del backoff su rate limit (429) del connettore Klaviyo: rispetta Retry-After e
ritenta, senza dormire davvero (time.sleep monkeypatchato).
"""
from src.connectors import klaviyo as kl


class _Resp:
    def __init__(self, status, headers=None, payload=None):
        self.status_code = status
        self.headers = headers or {}
        self._payload = payload or {}
        self.text = str(self._payload)

    def json(self):
        return self._payload


def test_429_then_200_respects_retry_after(monkeypatch):
    slept = []
    monkeypatch.setattr(kl.time, "sleep", lambda s: slept.append(s))

    seq = [
        _Resp(429, headers={"Retry-After": "30"}),
        _Resp(200, payload={"data": {"attributes": {"results": [{"ok": 1}]}}}),
    ]

    class _Sess:
        def request(self, *a, **k):
            return seq.pop(0)

    conn = kl.KlaviyoConnector(api_key="pk_test", revision="2024-10-15")
    conn._session = _Sess()
    data = conn._request("POST", "/campaign-values-reports/", json_body={})
    assert data["data"]["attributes"]["results"] == [{"ok": 1}]
    # ha rispettato il Retry-After (30s), sotto il cap di 60s
    assert slept == [30.0]


def test_429_backoff_capped_at_60s(monkeypatch):
    slept = []
    monkeypatch.setattr(kl.time, "sleep", lambda s: slept.append(s))
    seq = [
        _Resp(429, headers={"Retry-After": "600"}),   # oltre il cap
        _Resp(200, payload={"data": {"attributes": {"results": []}}}),
    ]

    class _Sess:
        def request(self, *a, **k):
            return seq.pop(0)

    conn = kl.KlaviyoConnector(api_key="pk_test")
    conn._session = _Sess()
    conn._request("GET", "/metrics/")
    assert slept == [60.0]                              # cap a 60s per tentativo
