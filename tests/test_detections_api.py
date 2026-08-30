"""GET /api/detections -- raw ingested detections, independent of any
track. Complements GET /api/tracks/{id}/history (tested in
tests/test_track_history.py), which needs a track id in hand already;
this is for sensor-level QA/debugging ("what has sensor X reported
recently") without one.
"""

from datetime import timedelta

from fastapi.testclient import TestClient

from app.main import app
from app.util import utcnow

DETECTION_BODY = {
    "sensor_id": "radar-1", "sensor_type": "radar",
    "latitude": 51.5, "longitude": -0.1, "confidence": 0.9,
}


def test_lists_ingested_detections():
    with TestClient(app) as client:
        assert client.post("/api/detections", json=DETECTION_BODY).status_code == 201
        assert client.post("/api/detections", json={**DETECTION_BODY, "latitude": 51.6}).status_code == 201

        r = client.get("/api/detections")
        assert r.status_code == 200
        body = r.json()
        assert len(body) == 2
        assert {d["sensor_id"] for d in body} == {"radar-1"}


def test_empty_when_nothing_ingested():
    with TestClient(app) as client:
        r = client.get("/api/detections")
        assert r.status_code == 200
        assert r.json() == []


def test_filters_by_sensor_id():
    with TestClient(app) as client:
        assert client.post("/api/detections", json=DETECTION_BODY).status_code == 201
        assert (
            client.post("/api/detections", json={**DETECTION_BODY, "sensor_id": "camera-1"}).status_code == 201
        )

        r = client.get("/api/detections", params={"sensor_id": "camera-1"})
        assert r.status_code == 200
        body = r.json()
        assert len(body) == 1
        assert body[0]["sensor_id"] == "camera-1"


def test_filters_by_time_range():
    # Both timestamps stay within MAX_DETECTION_CLOCK_SKEW_SECONDS of the
    # real current time -- ingest itself rejects anything further out
    # (app.api.detections._check_clock_skew), so a fixed calendar date
    # wouldn't reliably ingest at all.
    with TestClient(app) as client:
        now = utcnow()
        early = (now - timedelta(seconds=60)).isoformat()
        late = (now - timedelta(seconds=5)).isoformat()
        assert (
            client.post("/api/detections", json={**DETECTION_BODY, "timestamp": early, "latitude": 51.5}).status_code
            == 201
        )
        assert (
            client.post("/api/detections", json={**DETECTION_BODY, "timestamp": late, "latitude": 51.9}).status_code
            == 201
        )

        boundary = (now - timedelta(seconds=30)).isoformat()
        r = client.get("/api/detections", params={"start": boundary})
        assert r.status_code == 200
        body = r.json()
        assert len(body) == 1
        assert body[0]["latitude"] == 51.9


def test_respects_limit_and_offset():
    with TestClient(app) as client:
        for i in range(5):
            r = client.post("/api/detections", json={**DETECTION_BODY, "latitude": 51.5 + i * 0.001})
            assert r.status_code == 201

        first_page = client.get("/api/detections", params={"limit": 2, "offset": 0}).json()
        second_page = client.get("/api/detections", params={"limit": 2, "offset": 2}).json()
        assert len(first_page) == 2
        assert len(second_page) == 2
        assert {d["id"] for d in first_page}.isdisjoint({d["id"] for d in second_page})


def test_rejects_a_limit_over_the_cap():
    with TestClient(app) as client:
        r = client.get("/api/detections", params={"limit": 1001})
        assert r.status_code == 422
