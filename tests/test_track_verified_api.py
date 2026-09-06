"""GET /api/tracks / GET /api/tracks/{id} expose corroborating_sensor_types
and verified -- the same corroboration concept app.incidents already uses
to escalate severity (app.fusion.corroborating_sensor_type_count), now
also surfaced for the dashboard's Verified/Unverified track grouping.
"""

from fastapi.testclient import TestClient

from app.config import INCIDENT_CORROBORATION_MIN_SENSOR_TYPES
from app.main import app


def _detection(sensor_id: str, sensor_type: str, **overrides) -> dict:
    return {
        "sensor_id": sensor_id, "sensor_type": sensor_type,
        "latitude": 51.5, "longitude": -0.1, "confidence": 0.9,
        **overrides,
    }


def test_single_sensor_track_is_unverified(isolated_db):
    with TestClient(app) as client:
        r = client.post("/api/detections", json=_detection("radar-1", "radar"))
        track_id = r.json()["track_id"]

        track = client.get(f"/api/tracks/{track_id}").json()
        assert track["corroborating_sensor_types"] == 1
        assert track["verified"] is False
        assert track["contributing_sensor_types"] == ["radar"]


def test_contributing_sensor_types_lists_every_distinct_type(isolated_db):
    with TestClient(app) as client:
        r = client.post("/api/detections", json=_detection("radar-1", "radar"))
        track_id = r.json()["track_id"]
        client.post(
            "/api/detections",
            json=_detection("cam-1", "camera", latitude=51.5001, longitude=-0.1001),
        )

        track = client.get(f"/api/tracks/{track_id}").json()
        assert track["contributing_sensor_types"] == ["camera", "radar"]


def test_multi_sensor_track_is_verified(isolated_db):
    assert INCIDENT_CORROBORATION_MIN_SENSOR_TYPES == 2, (
        "This test's shape (exactly 2 distinct sensor types) assumes the default threshold -- "
        "update it if that default ever changes."
    )
    with TestClient(app) as client:
        r = client.post("/api/detections", json=_detection("radar-1", "radar"))
        track_id = r.json()["track_id"]
        client.post(
            "/api/detections",
            json=_detection("rf-1", "rf", latitude=51.5001, longitude=-0.1001),
        )

        track = client.get(f"/api/tracks/{track_id}").json()
        assert track["corroborating_sensor_types"] == 2
        assert track["verified"] is True


def test_get_tracks_list_also_reports_verified(isolated_db):
    with TestClient(app) as client:
        client.post("/api/detections", json=_detection("radar-1", "radar"))
        tracks = client.get("/api/tracks").json()
        assert len(tracks) == 1
        assert tracks[0]["verified"] is False
        assert tracks[0]["corroborating_sensor_types"] == 1


def test_classify_response_also_reports_verified(isolated_db):
    with TestClient(app) as client:
        r = client.post("/api/detections", json=_detection("radar-1", "radar"))
        track_id = r.json()["track_id"]

        classified = client.post(f"/api/tracks/{track_id}/classify", json={"classification": "friendly"})
        assert classified.json()["verified"] is False
        assert classified.json()["corroborating_sensor_types"] == 1
