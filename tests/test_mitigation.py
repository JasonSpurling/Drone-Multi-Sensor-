import json

import pytest

from app import mitigation
from app.models import Classification, Incident, IncidentSeverity, IncidentStatus, IncidentType, Track, TrackStatus
from app.util import utcnow


def make_incident(severity: IncidentSeverity) -> Incident:
    return Incident(
        incident_uid="uid-1",
        incident_type=IncidentType.ZONE_INCURSION,
        severity=severity,
        status=IncidentStatus.OPEN,
        track_id=42,
        zone_id=7,
        opened_at=utcnow(),
        description="Track 42 entered restricted zone 'Test Zone'",
    )


def make_track(latitude: float | None = 51.5, longitude: float | None = -0.1) -> Track:
    return Track(
        id=42, track_uid="trk-42", first_seen=utcnow(), last_seen=utcnow(),
        status=TrackStatus.ACTIVE, classification=Classification.DRONE,
        latitude=latitude, longitude=longitude, altitude_m=80.0,
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
    monkeypatch.setattr("app.mitigation.MITIGATION_WEBHOOK_URL", "")
    mitigation.notify_mitigation_system(make_incident(IncidentSeverity.CRITICAL), make_track())
    assert captured_requests == []


def test_sends_when_configured_and_above_threshold(captured_requests, monkeypatch):
    monkeypatch.setattr("app.mitigation.MITIGATION_WEBHOOK_URL", "https://mitigation.example/webhook")
    monkeypatch.setattr("app.mitigation.MITIGATION_MIN_SEVERITY", "high")
    mitigation.notify_mitigation_system(make_incident(IncidentSeverity.CRITICAL), make_track())
    assert len(captured_requests) == 1
    assert captured_requests[0].url == "https://mitigation.example/webhook"


def test_below_threshold_sends_nothing(captured_requests, monkeypatch):
    monkeypatch.setattr("app.mitigation.MITIGATION_WEBHOOK_URL", "https://mitigation.example/webhook")
    monkeypatch.setattr("app.mitigation.MITIGATION_MIN_SEVERITY", "critical")
    mitigation.notify_mitigation_system(make_incident(IncidentSeverity.MEDIUM), make_track())
    assert captured_requests == []


def test_track_with_no_position_sends_nothing(captured_requests, monkeypatch):
    monkeypatch.setattr("app.mitigation.MITIGATION_WEBHOOK_URL", "https://mitigation.example/webhook")
    monkeypatch.setattr("app.mitigation.MITIGATION_MIN_SEVERITY", "low")
    mitigation.notify_mitigation_system(
        make_incident(IncidentSeverity.CRITICAL), make_track(latitude=None, longitude=None)
    )
    assert captured_requests == []


def test_payload_carries_track_position_and_classification():
    payload = mitigation.build_mitigation_payload(make_incident(IncidentSeverity.HIGH), make_track())
    assert payload["track"]["latitude"] == 51.5
    assert payload["track"]["longitude"] == -0.1
    assert payload["track"]["classification"] == "drone"
    assert payload["severity"] == "high"
    assert payload["incident_uid"] == "uid-1"


def test_payload_is_json_serializable():
    payload = mitigation.build_mitigation_payload(make_incident(IncidentSeverity.HIGH), make_track())
    json.dumps(payload)  # must not raise
