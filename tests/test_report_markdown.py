"""
La riga sorgenti Telegram non deve contenere caratteri che rompono il Markdown v1
(underscore/asterischi non bilanciati nei nomi bucket) -> altrimenti l'intero messaggio
cade in plain text con gli asterischi visibili.
"""
from src.report import _source_label_md


def test_known_buckets_have_no_underscore():
    assert _source_label_md("google_paid") == "Google paid"
    assert _source_label_md("google_organic") == "Google organic"
    assert _source_label_md("meta") == "Meta"
    assert _source_label_md("email") == "Email"
    for bucket in ("google_paid", "google_organic", "email", "direct", "tiktok", "pinterest"):
        assert "_" not in _source_label_md(bucket)


def test_unknown_raw_sources_are_neutralized():
    # stringhe grezze sconosciute: nessun carattere speciale Markdown v1 residuo
    for raw in ("news_site.com", "utm_weird", "a*b", "c_d", "e`f", "g[h]"):
        out = _source_label_md(raw)
        assert not any(ch in out for ch in "_*[]`")
    assert _source_label_md("") == "other"
