from fastapi.testclient import TestClient

from app.main import app

DETECTION_BODY = {
    "sensor_id": "radar-1",
    "sensor_type": "radar",
    "latitude": 51.5,
    "longitude": -0.1,
    "confidence": 0.9,
}


def test_history_returns_every_detection_oldest_first():
    with TestClient(app) as client:
        r1 = client.post("/api/detections", json=DETECTION_BODY)
        assert r1.status_code == 201
        track_id = r1.json()["track_id"]

        r2 = client.post("/api/detections", json={**DETECTION_BODY, "latitude": 51.501})
        assert r2.status_code == 201
        assert r2.json()["track_id"] == track_id

        history = client.get(f"/api/tracks/{track_id}/history")
        assert history.status_code == 200
        rows = history.json()
        assert len(rows) == 2
        assert rows[0]["timestamp"] <= rows[1]["timestamp"]
        assert all(row["track_id"] == track_id for row in rows)


def test_history_survives_track_closure(monkeypatch):
    # A track's replay history must still be visible after the track has
    # aged out to "closed" -- this is the whole point of the endpoint.
    monkeypatch.setattr("app.tracking.TRACK_STALE_SECONDS", -1)
    monkeypatch.setattr("app.tracking.TRACK_DROP_SECONDS", -1)
    with TestClient(app) as client:
        r1 = client.post("/api/detections", json=DETECTION_BODY)
        track_id = r1.json()["track_id"]

        closed = client.get(f"/api/tracks/{track_id}")
        assert closed.json()["status"] == "closed"

        history = client.get(f"/api/tracks/{track_id}/history")
        assert history.status_code == 200
        assert len(history.json()) == 1


def test_history_for_unknown_track_is_404():
    with TestClient(app) as client:
        assert client.get("/api/tracks/999999/history").status_code == 404


def test_client_supplied_georeferenced_flag_is_discarded_on_ingest():
    # georeferenced must only ever be set server-side by app/georeference.py
    # -- if a client's claim of it were honored, they could get a signed
    # detection's signature (app/remote_id.py) to bind to None/None instead
    # of the position they actually reported, defeating position tampering
    # detection for GPS-reporting sensors too.
    with TestClient(app) as client:
        r = client.post("/api/detections", json={**DETECTION_BODY, "georeferenced": True})
        assert r.status_code == 201
        assert r.json()["georeferenced"] is False


def test_history_respects_limit():
    with TestClient(app) as client:
        r1 = client.post("/api/detections", json=DETECTION_BODY)
        track_id = r1.json()["track_id"]
        client.post("/api/detections", json={**DETECTION_BODY, "latitude": 51.501})

        history = client.get(f"/api/tracks/{track_id}/history", params={"limit": 1})
        assert len(history.json()) == 1
