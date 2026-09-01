"""PUT /api/detections/{id}/label and GET /api/ml/training-data/export --
the workflow that turns real accumulated sensor traffic into a CSV
app/ml/train.py can actually train from. See app/models.py's
Detection.human_label docstring for why this is independent of a track's
own (system-derived) classification.
"""

import pytest
from fastapi.testclient import TestClient

from app.db import create_detection
from app.main import app
from app.models import Detection, SensorType

DETECTION_BODY = {
    "sensor_id": "radar-1", "sensor_type": "radar",
    "latitude": 51.5, "longitude": -0.1, "confidence": 0.9,
}


def _ingest(client: TestClient, **overrides) -> dict:
    body = {**DETECTION_BODY, **overrides}
    r = client.post("/api/detections", json=body)
    assert r.status_code == 201
    return r.json()


def test_labeling_a_detection_sets_human_label():
    with TestClient(app) as client:
        detection = _ingest(client)
        r = client.put(f"/api/detections/{detection['id']}/label", json={"label": "drone"})
        assert r.status_code == 200
        assert r.json()["human_label"] == "drone"


def test_label_is_independent_of_track_classification():
    with TestClient(app) as client:
        detection = _ingest(client)
        assert detection["human_label"] is None
        r = client.put(f"/api/detections/{detection['id']}/label", json={"label": "bird"})
        # A radar detection at 0.9 confidence classifies as "drone" via the
        # rule-based classifier -- the human label doesn't have to agree,
        # and setting it must not silently change the track's own label.
        track_id = detection["track_id"]
        track = client.get(f"/api/tracks/{track_id}").json()
        assert r.json()["human_label"] == "bird"
        assert track["classification"] == "drone"


def test_label_can_be_cleared_by_passing_null():
    with TestClient(app) as client:
        detection = _ingest(client)
        client.put(f"/api/detections/{detection['id']}/label", json={"label": "aircraft"})
        r = client.put(f"/api/detections/{detection['id']}/label", json={"label": None})
        assert r.json()["human_label"] is None


def test_ingest_discards_a_client_supplied_human_label():
    # A sensor shouldn't get to supply its own training label at ingest
    # time -- only PUT /api/detections/{id}/label (a separate,
    # operator-only role) can set one.
    with TestClient(app) as client:
        detection = _ingest(client, human_label="drone")
        assert detection["human_label"] is None


def test_labeling_an_unknown_detection_404s():
    with TestClient(app) as client:
        r = client.put("/api/detections/999999/label", json={"label": "drone"})
        assert r.status_code == 404


def test_labeling_rejects_friendly():
    # FRIENDLY comes from a cryptographically verified authorized-operator
    # signature (app/allowlist.py), not something a human assigns here --
    # see app.models.TrainableLabel's docstring.
    with TestClient(app) as client:
        detection = _ingest(client)
        r = client.put(f"/api/detections/{detection['id']}/label", json={"label": "friendly"})
        assert r.status_code == 422


def test_labeling_records_an_audit_entry():
    with TestClient(app) as client:
        detection = _ingest(client)
        client.put(f"/api/detections/{detection['id']}/label", json={"label": "drone"})
        audit = client.get("/api/audit-log").json()
        assert any(entry["action"] == "detection.label" for entry in audit)


def test_export_includes_only_labeled_detections(site_id):
    from app.db import set_detection_human_label

    create_detection(
        Detection(
            site_id=site_id, sensor_id="radar-1", sensor_type=SensorType.RADAR, confidence=0.9,
        )
    )
    labeled = create_detection(
        Detection(
            site_id=site_id, sensor_id="radar-2", sensor_type=SensorType.RADAR, confidence=0.95,
        )
    )
    set_detection_human_label(labeled.id, site_id, "drone")
    with TestClient(app) as client:
        r = client.get("/api/ml/training-data/export")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/csv")
        rows = r.text.strip().splitlines()
        assert len(rows) == 2  # header + the one labeled detection
        assert "radar-2" not in rows[1]  # sensor_id isn't a training-CSV column at all
        assert "drone" in rows[1]
        assert str(labeled.confidence) in rows[1] or "0.95" in rows[1]


def test_export_extracts_rf_fields_from_raw_data(site_id):
    from app.db import set_detection_human_label

    detection = create_detection(
        Detection(
            site_id=site_id, sensor_id="rf-1", sensor_type=SensorType.RF, confidence=0.4,
            raw_data={"center_frequency_mhz": 2440.0, "bandwidth_mhz": 10.0, "frequency_hopping": True},
        )
    )
    set_detection_human_label(detection.id, site_id, "drone")
    with TestClient(app) as client:
        r = client.get("/api/ml/training-data/export")
        row = r.text.strip().splitlines()[1]
        assert "2440.0" in row
        assert "10.0" in row
        assert "true" in row


def test_export_is_empty_but_valid_csv_with_no_labeled_detections():
    with TestClient(app) as client:
        r = client.get("/api/ml/training-data/export")
        rows = r.text.strip().splitlines()
        expected_header = (
            "sensor_type,confidence,altitude_m,"
            "rf_center_frequency_mhz,rf_bandwidth_mhz,rf_frequency_hopping,label"
        )
        assert rows == [expected_header]


def test_export_output_is_directly_trainable(tmp_path, site_id):
    from app.db import set_detection_human_label

    pytest.importorskip("sklearn")

    labels = ["drone"] * 6 + ["bird"] * 6
    confidences = [0.9] * 6 + [0.1] * 6
    for i, (label, confidence) in enumerate(zip(labels, confidences, strict=True)):
        detection = create_detection(
            Detection(site_id=site_id, sensor_id=f"s{i}", sensor_type=SensorType.CAMERA, confidence=confidence)
        )
        set_detection_human_label(detection.id, site_id, label)
    with TestClient(app) as client:
        r = client.get("/api/ml/training-data/export")

    csv_path = tmp_path / "exported.csv"
    csv_path.write_text(r.text)

    from app.ml.train import train

    model_path = tmp_path / "model.joblib"
    train(str(csv_path), str(model_path), test_size=0.3, random_state=0)
    assert model_path.is_file()
