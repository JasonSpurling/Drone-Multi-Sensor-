import pytest
from fastapi.testclient import TestClient

from app.geo import destination_point
from app.main import app

CAMERA_BODY = {
    "sensor_type": "camera",
    "latitude": 51.5,
    "longitude": -0.1,
    "altitude_m": 10.0,
    "azimuth_reference_deg": 0.0,
}


def test_cue_computes_pan_and_tilt_toward_a_known_track_position():
    with TestClient(app) as client:
        client.put("/api/sensor-registrations/ptz-cam-1", json=CAMERA_BODY)

        target_lat, target_lon = destination_point(51.5, -0.1, bearing_deg=90.0, distance_m=1000.0)
        detection = {
            "sensor_id": "radar-1", "sensor_type": "radar",
            "latitude": target_lat, "longitude": target_lon, "altitude_m": 10.0, "confidence": 0.9,
        }
        r = client.post("/api/detections", json=detection)
        track_id = r.json()["track_id"]

        cue = client.get(f"/api/tracks/{track_id}/cue/ptz-cam-1")
        assert cue.status_code == 200
        body = cue.json()
        assert body["pan_deg"] == pytest.approx(90.0, abs=0.1)
        assert body["distance_m"] == pytest.approx(1000.0, abs=0.1)


def test_cue_for_unknown_track_is_404():
    with TestClient(app) as client:
        client.put("/api/sensor-registrations/ptz-cam-1", json=CAMERA_BODY)
        assert client.get("/api/tracks/999999/cue/ptz-cam-1").status_code == 404


def test_cue_for_unregistered_camera_is_404():
    with TestClient(app) as client:
        detection = {
            "sensor_id": "radar-1", "sensor_type": "radar",
            "latitude": 51.5, "longitude": -0.1, "confidence": 0.9,
        }
        r = client.post("/api/detections", json=detection)
        track_id = r.json()["track_id"]

        assert client.get(f"/api/tracks/{track_id}/cue/no-such-camera").status_code == 404


def test_cue_for_a_track_with_no_resolved_position_is_409():
    with TestClient(app) as client:
        client.put("/api/sensor-registrations/ptz-cam-1", json=CAMERA_BODY)
        # An azimuth/range detection from an unregistered sensor never
        # resolves to a lat/lon -- exactly the "no position yet" case.
        detection = {
            "sensor_id": "unregistered-radar", "sensor_type": "radar",
            "azimuth_deg": 45.0, "range_m": 500.0, "confidence": 0.9,
        }
        r = client.post("/api/detections", json=detection)
        track_id = r.json()["track_id"]

        assert client.get(f"/api/tracks/{track_id}/cue/ptz-cam-1").status_code == 409
