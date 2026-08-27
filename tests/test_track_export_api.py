from fastapi.testclient import TestClient

from app.main import app

DETECTION_BODY = {
    "sensor_id": "radar-1",
    "sensor_type": "radar",
    "latitude": 51.5,
    "longitude": -0.1,
    "confidence": 0.9,
}


def test_export_gpx_has_correct_content_type_and_filename():
    with TestClient(app) as client:
        r1 = client.post("/api/detections", json=DETECTION_BODY)
        track_id = r1.json()["track_id"]

        export = client.get(f"/api/tracks/{track_id}/history/export", params={"format": "gpx"})
        assert export.status_code == 200
        assert export.headers["content-type"].startswith("application/gpx+xml")
        assert "attachment" in export.headers["content-disposition"]
        assert "<trkpt" in export.text


def test_export_kml_has_correct_content_type():
    with TestClient(app) as client:
        r1 = client.post("/api/detections", json=DETECTION_BODY)
        track_id = r1.json()["track_id"]

        export = client.get(f"/api/tracks/{track_id}/history/export", params={"format": "kml"})
        assert export.status_code == 200
        assert export.headers["content-type"].startswith("application/vnd.google-earth.kml+xml")
        assert "<coordinates>" in export.text


def test_export_csv_has_correct_content_type():
    with TestClient(app) as client:
        r1 = client.post("/api/detections", json=DETECTION_BODY)
        track_id = r1.json()["track_id"]

        export = client.get(f"/api/tracks/{track_id}/history/export", params={"format": "csv"})
        assert export.status_code == 200
        assert export.headers["content-type"].startswith("text/csv")
        assert export.text.startswith("timestamp,sensor_id")


def test_export_rejects_invalid_format():
    with TestClient(app) as client:
        r1 = client.post("/api/detections", json=DETECTION_BODY)
        track_id = r1.json()["track_id"]

        export = client.get(f"/api/tracks/{track_id}/history/export", params={"format": "pdf"})
        assert export.status_code == 422


def test_export_for_unknown_track_is_404():
    with TestClient(app) as client:
        export = client.get("/api/tracks/999999/history/export", params={"format": "csv"})
        assert export.status_code == 404


def test_export_route_does_not_shadow_plain_history_endpoint():
    with TestClient(app) as client:
        r1 = client.post("/api/detections", json=DETECTION_BODY)
        track_id = r1.json()["track_id"]

        history = client.get(f"/api/tracks/{track_id}/history")
        assert history.status_code == 200
        assert isinstance(history.json(), list)
