"""API-level tests for the clock-skew sanity check (app/api/detections.py's
_check_clock_skew) -- a detection whose (client-supplied) timestamp is
implausibly far from the server's own clock must be rejected before it
ever reaches app.tracking's Kalman predict step, where an inflated dt
would balloon the predicted position's uncertainty.
"""

from datetime import timedelta

from fastapi.testclient import TestClient

from app.main import app
from app.util import utcnow

BASE_BODY = {
    "sensor_id": "radar-1", "sensor_type": "radar",
    "latitude": 51.5, "longitude": -0.1, "confidence": 0.9,
}


def test_detection_with_current_timestamp_is_accepted():
    with TestClient(app) as client:
        body = {**BASE_BODY, "timestamp": utcnow().isoformat()}
        r = client.post("/api/detections", json=body)
        assert r.status_code == 201


def test_detection_without_explicit_timestamp_is_accepted():
    # Falls back to Detection's default_factory=utcnow -- must not somehow
    # collide with the skew check.
    with TestClient(app) as client:
        r = client.post("/api/detections", json=BASE_BODY)
        assert r.status_code == 201


def test_detection_far_in_the_future_is_rejected():
    with TestClient(app) as client:
        skewed = utcnow() + timedelta(hours=1)
        body = {**BASE_BODY, "timestamp": skewed.isoformat()}
        r = client.post("/api/detections", json=body)
        assert r.status_code == 400
        assert "clock" in r.json()["detail"].lower()


def test_detection_far_in_the_past_is_rejected():
    with TestClient(app) as client:
        skewed = utcnow() - timedelta(hours=1)
        body = {**BASE_BODY, "timestamp": skewed.isoformat()}
        r = client.post("/api/detections", json=body)
        assert r.status_code == 400


def test_detection_just_within_tolerance_is_accepted():
    # MAX_DETECTION_CLOCK_SKEW_SECONDS defaults to 300s -- a few seconds
    # inside that must not be treated as skewed (real sensors/network have
    # some legitimate delay).
    with TestClient(app) as client:
        body = {**BASE_BODY, "timestamp": (utcnow() - timedelta(seconds=60)).isoformat()}
        r = client.post("/api/detections", json=body)
        assert r.status_code == 201


def test_batch_with_one_skewed_detection_rejects_the_whole_batch():
    with TestClient(app) as client:
        good = {**BASE_BODY, "timestamp": utcnow().isoformat()}
        skewed = {**BASE_BODY, "timestamp": (utcnow() + timedelta(hours=2)).isoformat()}
        r = client.post("/api/detections/batch", json=[good, skewed])
        assert r.status_code == 400
