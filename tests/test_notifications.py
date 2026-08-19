import json
import urllib.error

from app.incidents import _open_incident
from app.models import Classification, Incident, IncidentSeverity, IncidentStatus, IncidentType
from app.notifications import notify_incident


def make_incident(**overrides) -> Incident:
    defaults = dict(
        incident_uid="inc-1",
        incident_type=IncidentType.ZONE_INCURSION,
        severity=IncidentSeverity.HIGH,
        status=IncidentStatus.OPEN,
        track_id=1,
        zone_id=1,
        description="test incident",
    )
    defaults.update(overrides)
    return Incident(**defaults)


def test_no_webhooks_configured_does_nothing(monkeypatch):
    monkeypatch.setattr("app.notifications.WEBHOOK_URLS", [])
    calls = []
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: calls.append(a))
    notify_incident(make_incident())
    assert calls == []


def test_posts_incident_json_to_each_configured_webhook(monkeypatch):
    monkeypatch.setattr(
        "app.notifications.WEBHOOK_URLS", ["http://hook-one.example/x", "http://hook-two.example/y"]
    )
    posted = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(request, timeout):
        posted.append((request.full_url, json.loads(request.data)))
        return FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    incident = make_incident()
    notify_incident(incident)

    assert len(posted) == 2
    urls = [url for url, _ in posted]
    assert "http://hook-one.example/x" in urls
    assert "http://hook-two.example/y" in urls
    for _, body in posted:
        assert body["incident_uid"] == "inc-1"
        assert body["incident_type"] == "zone_incursion"


def test_one_failing_webhook_does_not_block_the_others(monkeypatch):
    monkeypatch.setattr(
        "app.notifications.WEBHOOK_URLS", ["http://dead.example/x", "http://alive.example/y"]
    )
    posted = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(request, timeout):
        if "dead" in request.full_url:
            raise urllib.error.URLError("connection refused")
        posted.append(request.full_url)
        return FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    notify_incident(make_incident())
    assert posted == ["http://alive.example/y"]


def test_open_incident_triggers_webhook(monkeypatch):
    # Integration point: opening a real incident through app.incidents
    # should invoke notify_incident, not just the standalone function.
    monkeypatch.setattr("app.notifications.WEBHOOK_URLS", ["http://hook.example/x"])
    posted = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(request, timeout):
        posted.append(request.full_url)
        return FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    from app.db import create_track
    from app.models import Track, TrackStatus
    from app.zones import zones_containing_point
    from datetime import datetime

    track = create_track(
        Track(
            track_uid="t1",
            first_seen=datetime(2026, 1, 1),
            last_seen=datetime(2026, 1, 1),
            status=TrackStatus.ACTIVE,
            classification=Classification.DRONE,
            latitude=51.1,
            longitude=0.0,
        )
    )
    from app.db import create_zone
    from app.models import Zone, ZoneType

    zone = create_zone(
        Zone(
            name="rz",
            zone_type=ZoneType.RESTRICTED,
            polygon=[(51.0, -0.1), (51.0, 0.1), (51.2, 0.1), (51.2, -0.1)],
        )
    )
    _open_incident(track, zone, IncidentType.ZONE_INCURSION, "test")
    assert posted == ["http://hook.example/x"]
