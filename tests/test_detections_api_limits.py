"""API-level tests for the ingest guardrails added alongside the load-test
report's findings: a max batch size (associate_detections_batch's
Hungarian assignment is superlinear in batch size, so an unbounded batch
is a resource-exhaustion shape) and a Retry-After header on 429s (so a
rate-limited sensor client doesn't have to guess a backoff).
"""

from fastapi.testclient import TestClient

from app.main import app

DETECTION_BODY = {
    "sensor_id": "radar-1", "sensor_type": "radar",
    "latitude": 51.5, "longitude": -0.1, "confidence": 0.9,
}


def test_batch_within_limit_is_accepted():
    with TestClient(app) as client:
        batch = [{**DETECTION_BODY, "latitude": 51.5 + i * 0.0001} for i in range(5)]
        r = client.post("/api/detections/batch", json=batch)
        assert r.status_code == 201


def test_batch_over_limit_is_rejected(monkeypatch):
    monkeypatch.setattr("app.api.detections.MAX_BATCH_SIZE", 3)
    with TestClient(app) as client:
        batch = [{**DETECTION_BODY, "latitude": 51.5 + i * 0.0001} for i in range(4)]
        r = client.post("/api/detections/batch", json=batch)
        assert r.status_code == 413
        assert "4" in r.json()["detail"]


def test_batch_over_limit_persists_nothing(monkeypatch):
    # An oversized batch must be rejected atomically, before any of its
    # detections are processed -- not partially ingested.
    from app.db import list_tracks

    monkeypatch.setattr("app.api.detections.MAX_BATCH_SIZE", 3)
    with TestClient(app) as client:
        batch = [{**DETECTION_BODY, "latitude": 51.5 + i * 0.0001} for i in range(4)]
        r = client.post("/api/detections/batch", json=batch)
        assert r.status_code == 413
    assert list_tracks() == []


def test_rate_limited_response_carries_retry_after_header(monkeypatch):
    monkeypatch.setattr("app.config.RATE_LIMIT_PER_SECOND", 1.0)
    monkeypatch.setattr("app.config.RATE_LIMIT_BURST", 1.0)
    from app.ratelimit import RateLimiter

    monkeypatch.setattr("app.api.detections.detection_rate_limiter", RateLimiter(1.0, 1.0))

    with TestClient(app) as client:
        first = client.post("/api/detections", json=DETECTION_BODY)
        assert first.status_code == 201

        second = client.post("/api/detections", json=DETECTION_BODY)
        assert second.status_code == 429
        assert "Retry-After" in second.headers
        assert int(second.headers["Retry-After"]) >= 1
