"""scripts/drone_cli.py's pure formatting/request-handling logic, loaded
the same way as tests/test_load_test_script.py -- the subcommand
functions themselves need a real running server (this file doesn't spin
one up; that was exercised manually), so what's covered here is what can
be tested in isolation: table rendering and error-response translation.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "drone_cli", Path(__file__).resolve().parent.parent / "scripts" / "drone_cli.py"
)
assert _SPEC is not None and _SPEC.loader is not None
drone_cli = importlib.util.module_from_spec(_SPEC)
sys.modules["drone_cli"] = drone_cli
_SPEC.loader.exec_module(drone_cli)


def test_print_table_with_no_rows(capsys):
    drone_cli._print_table([], ["id", "status"])
    assert capsys.readouterr().out.strip() == "(none)"


def test_print_table_renders_header_and_rows(capsys):
    rows = [{"id": 1, "status": "open"}, {"id": 2, "status": "resolved"}]
    drone_cli._print_table(rows, ["id", "status"])
    out = capsys.readouterr().out
    lines = out.strip().splitlines()
    assert lines[0].split() == ["id", "status"]
    assert "1" in lines[2]
    assert "open" in lines[2]


def test_print_table_widens_columns_to_fit_the_longest_value(capsys):
    rows = [{"id": 1, "status": "acknowledged"}]
    drone_cli._print_table(rows, ["id", "status"])
    out = capsys.readouterr().out
    data_line = out.strip().splitlines()[2]
    assert "acknowledged" in data_line
    assert len(data_line) >= len("acknowledged")


def test_print_table_missing_column_renders_blank(capsys):
    rows = [{"id": 1}]
    drone_cli._print_table(rows, ["id", "status"])
    out = capsys.readouterr().out
    assert "id" in out


class _FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.ok = status_code < 400
        self.text = str(payload)

    def json(self):
        return self._payload


def test_request_raises_system_exit_with_the_servers_detail_message(monkeypatch):
    monkeypatch.setattr(
        drone_cli.requests, "request", lambda *a, **k: _FakeResponse(404, {"detail": "Track not found"})
    )
    with pytest.raises(SystemExit, match="Track not found"):
        drone_cli._request("GET", "http://example.invalid", "/api/tracks/999")


def test_request_falls_back_to_raw_text_when_no_json_detail(monkeypatch):
    class _NonJsonResponse(_FakeResponse):
        def json(self):
            raise ValueError("not json")

    monkeypatch.setattr(drone_cli.requests, "request", lambda *a, **k: _NonJsonResponse(500, "boom"))
    with pytest.raises(SystemExit, match="boom"):
        drone_cli._request("GET", "http://example.invalid", "/api/tracks")


def test_request_returns_the_response_on_success(monkeypatch):
    response = _FakeResponse(200, [{"id": 1}])
    monkeypatch.setattr(drone_cli.requests, "request", lambda *a, **k: response)
    assert drone_cli._request("GET", "http://example.invalid", "/api/tracks") is response
