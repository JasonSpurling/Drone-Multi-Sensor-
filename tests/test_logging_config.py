"""app.logging_config: text (default) vs JSON structured logging, toggled
by DRONE_LOG_FORMAT.
"""

import json
import logging

import pytest

from app.logging_config import _JsonFormatter, configure_logging


@pytest.fixture(autouse=True)
def _restore_root_logger():
    # configure_logging() replaces the root logger's handlers wholesale --
    # restore them afterward so this doesn't clobber pytest's own caplog
    # handler for every test that runs after these.
    root = logging.getLogger()
    original_handlers = list(root.handlers)
    original_level = root.level
    yield
    root.handlers = original_handlers
    root.setLevel(original_level)


def _make_record(msg: str = "hello", exc_info=None) -> logging.LogRecord:
    return logging.LogRecord(
        name="app.test", level=logging.INFO, pathname=__file__, lineno=1,
        msg=msg, args=(), exc_info=exc_info,
    )


def test_json_formatter_produces_valid_json_with_expected_fields():
    formatter = _JsonFormatter()
    record = _make_record("something happened")
    payload = json.loads(formatter.format(record))
    assert payload["level"] == "INFO"
    assert payload["logger"] == "app.test"
    assert payload["message"] == "something happened"
    assert "timestamp" in payload
    assert "exc_info" not in payload


def test_json_formatter_includes_exception_info_when_present():
    formatter = _JsonFormatter()
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        record = _make_record("failed", exc_info=sys.exc_info())
    payload = json.loads(formatter.format(record))
    assert "ValueError: boom" in payload["exc_info"]


def test_configure_logging_uses_json_formatter_when_configured(monkeypatch):
    monkeypatch.setattr("app.logging_config.LOG_FORMAT", "json")
    configure_logging()
    root = logging.getLogger()
    assert isinstance(root.handlers[0].formatter, _JsonFormatter)


def test_configure_logging_uses_text_formatter_by_default(monkeypatch):
    monkeypatch.setattr("app.logging_config.LOG_FORMAT", "text")
    configure_logging()
    root = logging.getLogger()
    assert not isinstance(root.handlers[0].formatter, _JsonFormatter)
    assert isinstance(root.handlers[0].formatter, logging.Formatter)


def test_configure_logging_applies_the_configured_level(monkeypatch):
    monkeypatch.setattr("app.logging_config.LOG_LEVEL", "WARNING")
    configure_logging()
    assert logging.getLogger().level == logging.WARNING
