"""app.main._warn_if_insecure_startup -- this app never terminates TLS
itself (see its docstring), so the one combination worth a loud warning is
"reachable beyond this machine, and anyone who reaches it is trusted by
default": DRONE_HOST bound non-loopback with no API keys configured.
"""

import logging

from app.main import _warn_if_insecure_startup


def test_warns_when_bound_non_loopback_with_no_keys_configured(monkeypatch, caplog):
    monkeypatch.setattr("app.config.HOST", "0.0.0.0")
    monkeypatch.setattr("app.config.API_KEY", "")
    monkeypatch.setattr("app.config.API_KEYS_JSON", "")

    with caplog.at_level(logging.WARNING):
        _warn_if_insecure_startup()

    assert any("no DRONE_API_KEY/DRONE_API_KEYS configured" in r.message for r in caplog.records)


def test_no_warning_when_bound_to_loopback(monkeypatch, caplog):
    monkeypatch.setattr("app.config.HOST", "127.0.0.1")
    monkeypatch.setattr("app.config.API_KEY", "")
    monkeypatch.setattr("app.config.API_KEYS_JSON", "")

    with caplog.at_level(logging.WARNING):
        _warn_if_insecure_startup()

    assert caplog.records == []


def test_no_warning_when_non_loopback_but_a_legacy_key_is_configured(monkeypatch, caplog):
    monkeypatch.setattr("app.config.HOST", "0.0.0.0")
    monkeypatch.setattr("app.config.API_KEY", "some-secret")
    monkeypatch.setattr("app.config.API_KEYS_JSON", "")

    with caplog.at_level(logging.WARNING):
        _warn_if_insecure_startup()

    assert caplog.records == []


def test_no_warning_when_non_loopback_but_api_keys_json_is_configured(monkeypatch, caplog):
    monkeypatch.setattr("app.config.HOST", "0.0.0.0")
    monkeypatch.setattr("app.config.API_KEY", "")
    monkeypatch.setattr("app.config.API_KEYS_JSON", '{"some-key": "admin"}')

    with caplog.at_level(logging.WARNING):
        _warn_if_insecure_startup()

    assert caplog.records == []
