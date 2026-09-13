"""app.notifications.notify_incident -- outbound webhook alerting fired
whenever an incident opens. Structurally identical to app.mitigation's
webhook notifier (best-effort, short-timeout, swallows delivery failures)
but fans out to every configured URL instead of one.
"""

import pytest

from app import notifications
from app.models import Incident, IncidentSeverity, IncidentStatus, IncidentType
from app.util import utcnow


def make_incident() -> Incident:
    return Incident(
        incident_uid="uid-1", incident_type=IncidentType.ZONE_INCURSION, severity=IncidentSeverity.HIGH,
        status=IncidentStatus.OPEN, track_id=42, zone_id=7, opened_at=utcnow(),
        description="Track 42 entered restricted zone 'Test Zone'",
    )


class _CapturedRequest:
    def __init__(self, request):
        self.url = request.full_url
        self.body = request.data


@pytest.fixture
def captured_requests(monkeypatch):
    calls = []

    def fake_urlopen(request, timeout=None):
        calls.append(_CapturedRequest(request))

        class _Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        return _Response()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    return calls


def test_disabled_by_default_sends_nothing(captured_requests, monkeypatch):
    monkeypatch.setattr("app.notifications.WEBHOOK_URLS", [])
    notifications.notify_incident(make_incident())
    assert captured_requests == []


def test_sends_to_every_configured_url(captured_requests, monkeypatch):
    monkeypatch.setattr(
        "app.notifications.WEBHOOK_URLS", ["https://hooks.example/one", "https://hooks.example/two"]
    )
    notifications.notify_incident(make_incident())
    assert [r.url for r in captured_requests] == ["https://hooks.example/one", "https://hooks.example/two"]


def test_payload_carries_the_incident_as_json(captured_requests, monkeypatch):
    monkeypatch.setattr("app.notifications.WEBHOOK_URLS", ["https://hooks.example/one"])
    notifications.notify_incident(make_incident())
    assert b'"incident_uid": "uid-1"' in captured_requests[0].body or (
        b'"incident_uid":"uid-1"' in captured_requests[0].body
    )


def test_a_failing_url_does_not_stop_delivery_to_the_rest(monkeypatch, caplog):
    monkeypatch.setattr(
        "app.notifications.WEBHOOK_URLS", ["https://hooks.example/dead", "https://hooks.example/alive"]
    )
    calls = []

    def sometimes_failing_urlopen(request, timeout=None):
        calls.append(request.full_url)
        if request.full_url == "https://hooks.example/dead":
            raise OSError("connection refused")

        class _Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        return _Response()

    monkeypatch.setattr("urllib.request.urlopen", sometimes_failing_urlopen)

    with caplog.at_level("WARNING"):
        notifications.notify_incident(make_incident())  # must not raise

    assert calls == ["https://hooks.example/dead", "https://hooks.example/alive"]
    assert "Webhook notification to https://hooks.example/dead failed" in caplog.text
