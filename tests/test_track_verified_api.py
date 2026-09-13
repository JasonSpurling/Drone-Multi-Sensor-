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


def test_risk_score_is_exposed_and_reflects_an_open_incident(isolated_db):
    """app.risk.compute_risk_score wired into GET /api/tracks -- a track
    with no open incidents should be low (its classification alone), and
    jump once a real zone-incursion incident opens for it.
    """
    with TestClient(app) as client:
        # Just outside, then just inside, app/zones.seed.json's bundled
        # "Central London Restricted Zone" (51.49-51.51, -0.11--0.09),
        # which isolated_db's default seed always loads -- within
        # TRACK_DISTANCE_GATE_M (500m) so the tracker fuses both
        # detections into one track (see test_track_ignore_api.py's
        # identical reasoning for these exact coordinates).
        r = client.post("/api/detections", json=_detection("radar-1", "radar", latitude=51.5101, longitude=-0.10))
        track_id = r.json()["track_id"]
        baseline = client.get(f"/api/tracks/{track_id}").json()
        assert baseline["risk_score"] is not None

        client.post("/api/detections", json=_detection("radar-1", "radar", latitude=51.5099, longitude=-0.10))
        after = client.get(f"/api/tracks/{track_id}").json()
        assert after["risk_score"] > baseline["risk_score"]


def test_risk_score_reflects_proximity_to_a_restricted_zone(isolated_db):
    """app.zones.nearest_restricted_zone_distance_m wired into
    compute_risk_score via GET /api/tracks -- a track well outside any
    zone should score lower than the same track once it's near the
    seeded restricted zone's centroid.
    """
    with TestClient(app) as client:
        far = client.post("/api/detections", json=_detection("radar-1", "radar", latitude=60.0, longitude=0.0))
        far_track_id = far.json()["track_id"]
        far_score = client.get(f"/api/tracks/{far_track_id}").json()["risk_score"]

        # app/zones.seed.json's bundled "Central London Restricted Zone"
        # centroid is ~(51.5, -0.1) -- isolated_db's default seed always
        # loads it.
        near = client.post("/api/detections", json=_detection("radar-2", "radar", latitude=51.5, longitude=-0.1))
        near_track_id = near.json()["track_id"]
        near_score = client.get(f"/api/tracks/{near_track_id}").json()["risk_score"]

        assert near_score > far_score


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


def test_risk_factors_lists_the_real_reasons_behind_the_score(isolated_db):
    with TestClient(app) as client:
        r = client.post("/api/detections", json=_detection("radar-1", "radar"))
        track_id = r.json()["track_id"]
        track = client.get(f"/api/tracks/{track_id}").json()
        assert track["risk_score"] > 0
        assert any("drone" in f.lower() for f in track["risk_factors"])


def test_zone_status_is_inside_within_the_seeded_restricted_zone(isolated_db):
    with TestClient(app) as client:
        # app/zones.seed.json's "Central London Restricted Zone" -- see
        # test_track_ignore_api.py's identical coordinate reasoning.
        r = client.post("/api/detections", json=_detection("radar-1", "radar", latitude=51.5, longitude=-0.1))
        track_id = r.json()["track_id"]
        assert client.get(f"/api/tracks/{track_id}").json()["zone_status"] == "inside"


def test_zone_status_is_none_far_from_any_zone(isolated_db):
    with TestClient(app) as client:
        r = client.post("/api/detections", json=_detection("radar-1", "radar", latitude=10.0, longitude=10.0))
        track_id = r.json()["track_id"]
        assert client.get(f"/api/tracks/{track_id}").json()["zone_status"] == "none"
