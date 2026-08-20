from datetime import datetime

from fastapi.testclient import TestClient

from app.db import create_incident, create_track, create_zone
from app.main import app
from app.models import (
    Classification,
    Incident,
    IncidentSeverity,
    IncidentStatus,
    IncidentType,
    Track,
    TrackStatus,
    Zone,
    ZoneType,
)

SQUARE = [(51.0, -0.1), (51.0, 0.1), (51.2, 0.1), (51.2, -0.1)]


def _seed_incident(opened_at: datetime, severity=IncidentSeverity.MEDIUM) -> Incident:
    zone = create_zone(Zone(name="rz", zone_type=ZoneType.RESTRICTED, polygon=SQUARE))
    track = create_track(
        Track(
            track_uid=f"track-{opened_at.isoformat()}", first_seen=opened_at, last_seen=opened_at,
            status=TrackStatus.ACTIVE, classification=Classification.DRONE,
            latitude=51.1, longitude=0.0, altitude_m=100,
        )
    )
    return create_incident(
        Incident(
            incident_uid=f"uid-{opened_at.isoformat()}",
            incident_type=IncidentType.ZONE_INCURSION,
            severity=severity,
            status=IncidentStatus.OPEN,
            track_id=track.id,
            zone_id=zone.id,
            opened_at=opened_at,
        )
    )


def test_report_counts_incidents_within_range():
    with TestClient(app) as client:
        _seed_incident(datetime(2026, 1, 5))
        _seed_incident(datetime(2026, 1, 6))
        _seed_incident(datetime(2026, 2, 1))  # outside range, must not be counted

        r = client.get("/api/reports/incidents", params={"start": "2026-01-01", "end": "2026-01-31"})
        assert r.status_code == 200
        assert r.json()["total_incidents"] == 2


def test_report_rejects_end_before_start():
    with TestClient(app) as client:
        r = client.get("/api/reports/incidents", params={"start": "2026-01-31", "end": "2026-01-01"})
        assert r.status_code == 400


def test_report_rejects_malformed_date():
    with TestClient(app) as client:
        r = client.get("/api/reports/incidents", params={"start": "not-a-date", "end": "2026-01-31"})
        assert r.status_code == 400


def test_export_returns_csv_of_incidents_in_range():
    with TestClient(app) as client:
        _seed_incident(datetime(2026, 1, 5))

        r = client.get("/api/reports/incidents/export", params={"start": "2026-01-01", "end": "2026-01-31"})
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/csv")
        assert "attachment" in r.headers["content-disposition"]
        assert "incident_uid" in r.text
