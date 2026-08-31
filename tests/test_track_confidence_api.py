"""GET /api/tracks / GET /api/tracks/{id} apply read-time staleness decay
(app.fusion.decay_classification_confidence) to the classification_confidence
app.tracking stored as of the track's last detection -- see
app/api/tracks.py's _with_decayed_confidence.
"""

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.config import CLASSIFICATION_CONFIDENCE_DECAY_SECONDS, CLASSIFICATION_CONFIDENCE_FLOOR
from app.main import app

DETECTION_BODY = {
    "sensor_id": "camera-1", "sensor_type": "camera",
    "latitude": 51.5, "longitude": -0.1, "confidence": 0.95,
}


def test_fresh_track_has_undamped_confidence():
    with TestClient(app) as client:
        r = client.post("/api/detections", json=DETECTION_BODY)
        assert r.status_code == 201
        track_id = r.json()["track_id"]

        track = client.get(f"/api/tracks/{track_id}").json()
        # approx, not exact: real wall-clock time (this test doesn't
        # monkeypatch "now") elapses between ingest and this GET, so a
        # sliver of decay has genuinely already happened.
        assert track["classification_confidence"] == pytest.approx(1.0, abs=0.01)


def test_stale_track_shows_decayed_confidence_via_get_track_by_id(monkeypatch):
    with TestClient(app) as client:
        r = client.post("/api/detections", json=DETECTION_BODY)
        track_id = r.json()["track_id"]
        last_seen = datetime.fromisoformat(r.json()["timestamp"])

        future = last_seen + timedelta(seconds=CLASSIFICATION_CONFIDENCE_DECAY_SECONDS)
        monkeypatch.setattr("app.api.tracks.utcnow", lambda: future)

        stale = client.get(f"/api/tracks/{track_id}").json()
        assert stale["classification_confidence"] == pytest.approx(CLASSIFICATION_CONFIDENCE_FLOOR)


def test_stale_track_shows_decayed_confidence_via_list_tracks(monkeypatch):
    with TestClient(app) as client:
        r = client.post("/api/detections", json=DETECTION_BODY)
        last_seen = datetime.fromisoformat(r.json()["timestamp"])

        future = last_seen + timedelta(seconds=CLASSIFICATION_CONFIDENCE_DECAY_SECONDS / 2)
        monkeypatch.setattr("app.api.tracks.utcnow", lambda: future)

        tracks = client.get("/api/tracks").json()
        assert len(tracks) == 1
        assert CLASSIFICATION_CONFIDENCE_FLOOR < tracks[0]["classification_confidence"] < 1.0


def test_decay_never_changes_the_classification_label_itself(monkeypatch):
    with TestClient(app) as client:
        r = client.post("/api/detections", json=DETECTION_BODY)
        track_id = r.json()["track_id"]
        last_seen = datetime.fromisoformat(r.json()["timestamp"])
        fresh = client.get(f"/api/tracks/{track_id}").json()

        monkeypatch.setattr(
            "app.api.tracks.utcnow", lambda: last_seen + timedelta(seconds=CLASSIFICATION_CONFIDENCE_DECAY_SECONDS * 5)
        )
        stale = client.get(f"/api/tracks/{track_id}").json()
        assert stale["classification"] == fresh["classification"]
        assert stale["classification_confidence"] < fresh["classification_confidence"]
