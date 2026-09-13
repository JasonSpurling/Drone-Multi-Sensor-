"""POST /api/tracks/{id}/ignore -- an operator's "stop alerting on this"
suppression. See app/models.py's Track.ignored/TrackIgnoreInput and
app/api/tracks.py's ignore_track for what this actually changes (new
incidents only -- the track itself keeps updating and showing up
everywhere else unchanged).
"""

import json
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.util import utcnow

DETECTION_BODY = {
    "sensor_id": "radar-1", "sensor_type": "radar",
    "latitude": 51.5, "longitude": -0.1, "confidence": 0.9,
}

# Just outside, then just inside, app/zones.seed.json's bundled "Central
# London Restricted Zone" (51.49-51.51, -0.11--0.09), which isolated_db's
# default seed always loads -- within TRACK_DISTANCE_GATE_M (500m, ~0.0045
# degrees latitude) of each other so the tracker fuses both detections
# into one track (a bigger jump would instead start a second, separate
# track), but on opposite sides of the zone boundary, so the incident-
# suppression test below can isolate "ignoring suppresses the incident"
# from "there was never going to be one."
OUTSIDE_ANY_ZONE = {**DETECTION_BODY, "latitude": 51.5101, "longitude": -0.10}
INSIDE_SEED_ZONE = {**DETECTION_BODY, "latitude": 51.5099, "longitude": -0.10}


def test_ignore_sets_the_flag_and_is_persisted(isolated_db):
    with TestClient(app) as client:
        r = client.post("/api/detections", json=OUTSIDE_ANY_ZONE)
        track_id = r.json()["track_id"]
        assert client.get(f"/api/tracks/{track_id}").json()["ignored"] is False

        ignored = client.post(f"/api/tracks/{track_id}/ignore", json={"ignored": True})
        assert ignored.status_code == 200
        assert ignored.json()["ignored"] is True

        fetched = client.get(f"/api/tracks/{track_id}").json()
        assert fetched["ignored"] is True


def test_unignore_toggles_it_back_off(isolated_db):
    with TestClient(app) as client:
        r = client.post("/api/detections", json=DETECTION_BODY)
        track_id = r.json()["track_id"]
        client.post(f"/api/tracks/{track_id}/ignore", json={"ignored": True})

        unignored = client.post(f"/api/tracks/{track_id}/ignore", json={"ignored": False})
        assert unignored.status_code == 200
        assert unignored.json()["ignored"] is False


def test_ignore_indefinitely_leaves_ignored_until_null(isolated_db):
    with TestClient(app) as client:
        r = client.post("/api/detections", json=OUTSIDE_ANY_ZONE)
        track_id = r.json()["track_id"]

        ignored = client.post(f"/api/tracks/{track_id}/ignore", json={"ignored": True})
        assert ignored.json()["ignored"] is True
        assert ignored.json()["ignored_until"] is None


def test_ignore_with_a_duration_sets_a_real_expiry(isolated_db):
    with TestClient(app) as client:
        r = client.post("/api/detections", json=OUTSIDE_ANY_ZONE)
        track_id = r.json()["track_id"]

        before = utcnow()
        ignored = client.post(
            f"/api/tracks/{track_id}/ignore", json={"ignored": True, "duration_minutes": 30}
        )
        assert ignored.json()["ignored"] is True
        ignored_until = datetime.fromisoformat(ignored.json()["ignored_until"])
        assert before + timedelta(minutes=29) < ignored_until < before + timedelta(minutes=31)


def test_a_timed_ignore_expires_on_its_own(isolated_db, monkeypatch):
    """No separate expiry-sweep job exists -- app.db._row_to_track resolves
    an expired ignored_until back to False on every read, checked here via
    the shared app.db.utcnow used at that read site.
    """
    with TestClient(app) as client:
        r = client.post("/api/detections", json=OUTSIDE_ANY_ZONE)
        track_id = r.json()["track_id"]
        client.post(f"/api/tracks/{track_id}/ignore", json={"ignored": True, "duration_minutes": 5})
        assert client.get(f"/api/tracks/{track_id}").json()["ignored"] is True

        future = utcnow() + timedelta(minutes=6)
        monkeypatch.setattr("app.db.utcnow", lambda: future)
        assert client.get(f"/api/tracks/{track_id}").json()["ignored"] is False


def test_unignore_clears_any_existing_expiry(isolated_db):
    with TestClient(app) as client:
        r = client.post("/api/detections", json=OUTSIDE_ANY_ZONE)
        track_id = r.json()["track_id"]
        client.post(f"/api/tracks/{track_id}/ignore", json={"ignored": True, "duration_minutes": 30})

        unignored = client.post(f"/api/tracks/{track_id}/ignore", json={"ignored": False})
        assert unignored.json()["ignored"] is False
        assert unignored.json()["ignored_until"] is None


def test_ignore_unknown_track_is_404(isolated_db):
    with TestClient(app) as client:
        r = client.post("/api/tracks/999999/ignore", json={"ignored": True})
        assert r.status_code == 404


def test_an_ignored_track_never_opens_a_zone_incursion_incident(isolated_db):
    with TestClient(app) as client:
        r = client.post("/api/detections", json=OUTSIDE_ANY_ZONE)
        track_id = r.json()["track_id"]
        client.post(f"/api/tracks/{track_id}/ignore", json={"ignored": True})

        # Move the now-ignored track into the seed zone -- without the
        # ignore above, this is exactly what opens a zone_incursion
        # incident (see test_incidents.py::test_incident_opened_for_...).
        client.post("/api/detections", json=INSIDE_SEED_ZONE)

        incidents = client.get("/api/incidents").json()
        assert incidents == []


@pytest.fixture
def keys(monkeypatch):
    mapping = {"ingest-key": "ingest", "view-key": "viewer", "ops-key": "operator"}
    monkeypatch.setattr("app.config.API_KEY", "")
    monkeypatch.setattr("app.config.API_KEYS_JSON", json.dumps(mapping))
    return mapping


def test_viewer_cannot_ignore_operator_can(isolated_db, keys):
    with TestClient(app) as client:
        r = client.post("/api/detections", json=DETECTION_BODY, headers={"X-API-Key": "ingest-key"})
        track_id = r.json()["track_id"]

        denied = client.post(
            f"/api/tracks/{track_id}/ignore", json={"ignored": True}, headers={"X-API-Key": "view-key"}
        )
        assert denied.status_code == 403

        allowed = client.post(
            f"/api/tracks/{track_id}/ignore", json={"ignored": True}, headers={"X-API-Key": "ops-key"}
        )
        assert allowed.status_code == 200
