import argparse
import io
import json
import urllib.error

import pytest

from app.adapters.sdk import add_common_post_args, format_post_error, post_detection


class _FakeHTTPResponse:
    def __init__(self, body: dict):
        self._body = json.dumps(body).encode()

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_post_detection_sends_api_key_header_and_returns_decoded_json(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["headers"] = dict(request.header_items())
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        return _FakeHTTPResponse({"id": 1, "track_id": 2})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    result = post_detection("http://example.invalid/api/detections", {"sensor_id": "x"}, api_key="secret")

    assert result == {"id": 1, "track_id": 2}
    assert captured["headers"]["X-api-key"] == "secret"
    assert captured["url"] == "http://example.invalid/api/detections"


def test_post_detection_omits_header_when_no_api_key(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["headers"] = dict(request.header_items())
        return _FakeHTTPResponse({"id": 1})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    post_detection("http://example.invalid/api/detections", {"sensor_id": "x"})
    assert "X-api-key" not in captured["headers"]


def test_post_detection_raises_immediately_with_zero_retries(monkeypatch):
    calls = []

    def fake_urlopen(request, timeout):
        calls.append(1)
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    with pytest.raises(urllib.error.URLError):
        post_detection("http://example.invalid/api/detections", {}, max_retries=0)
    assert len(calls) == 1


def test_post_detection_retries_then_succeeds(monkeypatch):
    calls = []

    def fake_urlopen(request, timeout):
        calls.append(1)
        if len(calls) < 3:
            raise urllib.error.URLError("temporary failure")
        return _FakeHTTPResponse({"ok": True})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("time.sleep", lambda s: None)
    result = post_detection("http://example.invalid/api/detections", {}, max_retries=3, retry_backoff_s=0.01)
    assert result == {"ok": True}
    assert len(calls) == 3


def test_post_detection_raises_after_exhausting_retries(monkeypatch):
    calls = []

    def fake_urlopen(request, timeout):
        calls.append(1)
        raise urllib.error.URLError("still down")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("time.sleep", lambda s: None)
    with pytest.raises(urllib.error.URLError):
        post_detection("http://example.invalid/api/detections", {}, max_retries=2, retry_backoff_s=0.01)
    assert len(calls) == 3  # initial attempt + 2 retries


def test_add_common_post_args_wires_expected_defaults(monkeypatch):
    monkeypatch.delenv("DRONE_API_KEY", raising=False)
    parser = argparse.ArgumentParser()
    add_common_post_args(parser, default_sensor_id="test-sensor-1")
    args = parser.parse_args([])
    assert args.sensor_id == "test-sensor-1"
    assert args.api_url == "http://127.0.0.1:8000/api/detections"
    assert args.api_key == ""
    assert args.max_retries == 0


def test_add_common_post_args_reads_api_key_from_env(monkeypatch):
    monkeypatch.setenv("DRONE_API_KEY", "env-key")
    parser = argparse.ArgumentParser()
    add_common_post_args(parser, default_sensor_id="test-sensor-1")
    args = parser.parse_args([])
    assert args.api_key == "env-key"


def test_add_common_post_args_overridable_from_cli(monkeypatch):
    monkeypatch.delenv("DRONE_API_KEY", raising=False)
    parser = argparse.ArgumentParser()
    add_common_post_args(parser, default_sensor_id="test-sensor-1")
    args = parser.parse_args(["--sensor-id", "other", "--max-retries", "5"])
    assert args.sensor_id == "other"
    assert args.max_retries == 5


def test_format_post_error_includes_the_response_body_for_an_http_error():
    # Plain str(exc) on an HTTPError drops the JSON body FastAPI actually
    # sends (e.g. the reason an API key was rejected) -- that body is
    # exactly what an operator needs to see to fix the problem.
    exc = urllib.error.HTTPError(
        url="http://x/api/detections", code=401, msg="Unauthorized",
        hdrs=None, fp=io.BytesIO(b'{"detail": "Missing X-API-Key header"}'),
    )
    message = format_post_error(exc)
    assert "401" in message
    assert "Missing X-API-Key header" in message


def test_format_post_error_falls_back_to_str_for_a_non_http_error():
    exc = urllib.error.URLError("Connection refused")
    assert format_post_error(exc) == str(exc)


def test_format_post_error_survives_a_body_that_fails_to_read(monkeypatch):
    # The response body isn't always readable a second time (already
    # consumed, connection dropped) -- must fall back to no body rather
    # than raise out of error formatting itself.
    exc = urllib.error.HTTPError(
        url="http://x/api/detections", code=401, msg="Unauthorized", hdrs=None, fp=io.BytesIO(b"")
    )

    def raise_oserror():
        raise OSError("connection already closed")

    monkeypatch.setattr(exc, "read", raise_oserror)
    message = format_post_error(exc)
    assert message == "HTTP 401 Unauthorized"
