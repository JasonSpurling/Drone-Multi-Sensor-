"""POST /api/tracks/{id}/classify -- an operator's deliberate override of
a track's classification, distinct from app.tracking's automated fusion
ratchet. See app/models.py's TrackClassificationInput and
app/api/tracks.py's classify_track for why this exists as its own
endpoint rather than a generic PATCH.
"""

import json

import pytest
from fastapi.testclient import TestClient

from app.main import app

DETECTION_BODY = {
    "sensor_id": "radar-1", "sensor_type": "radar",
    "latitude": 51.5, "longitude": -0.1, "confidence": 0.9,
}


def test_classify_as_friendly_sets_classification_and_full_confidence(isolated_db):
    with TestClient(app) as client:
        r = client.post("/api/detections", json=DETECTION_BODY)
        track_id = r.json()["track_id"]

        classified = client.post(f"/api/tracks/{track_id}/classify", json={"classification": "friendly"})
        assert classified.status_code == 200
        body = classified.json()
        assert body["classification"] == "friendly"
        # approx, not exact: GET-time staleness decay (see
        # app.fusion.decay_classification_confidence) has already nibbled
        # a sliver off by the time this response serializes, same as
        # test_track_confidence_api.py's identical real-wall-clock caveat.
        assert body["classification_confidence"] == pytest.approx(1.0, abs=0.01)

        # Persisted, not just returned in the response.
        fetched = client.get(f"/api/tracks/{track_id}").json()
        assert fetched["classification"] == "friendly"


def test_classify_as_drone_works_too(isolated_db):
    with TestClient(app) as client:
        r = client.post("/api/detections", json=DETECTION_BODY)
        track_id = r.json()["track_id"]

        classified = client.post(f"/api/tracks/{track_id}/classify", json={"classification": "drone"})
        assert classified.status_code == 200
        assert classified.json()["classification"] == "drone"


def test_classify_rejects_values_outside_friendly_or_drone(isolated_db):
    with TestClient(app) as client:
        r = client.post("/api/detections", json=DETECTION_BODY)
        track_id = r.json()["track_id"]

        # Reclassifying as "bird" or "aircraft" is what sensor evidence is
        # for, not an operator override -- see TrackClassificationInput's
        # docstring for why this endpoint restricts to friendly/drone only.
        rejected = client.post(f"/api/tracks/{track_id}/classify", json={"classification": "bird"})
        assert rejected.status_code == 422


def test_classify_unknown_track_is_404(isolated_db):
    with TestClient(app) as client:
        r = client.post("/api/tracks/999999/classify", json={"classification": "friendly"})
        assert r.status_code == 404


@pytest.fixture
def keys(monkeypatch):
    mapping = {"ingest-key": "ingest", "view-key": "viewer", "ops-key": "operator"}
    monkeypatch.setattr("app.config.API_KEY", "")
    monkeypatch.setattr("app.config.API_KEYS_JSON", json.dumps(mapping))
    return mapping


def test_viewer_cannot_classify_operator_can(isolated_db, keys):
    with TestClient(app) as client:
        r = client.post("/api/detections", json=DETECTION_BODY, headers={"X-API-Key": "ingest-key"})
        track_id = r.json()["track_id"]

        denied = client.post(
            f"/api/tracks/{track_id}/classify",
            json={"classification": "friendly"},
            headers={"X-API-Key": "view-key"},
        )
        assert denied.status_code == 403

        allowed = client.post(
            f"/api/tracks/{track_id}/classify",
            json={"classification": "friendly"},
            headers={"X-API-Key": "ops-key"},
        )
        assert allowed.status_code == 200
