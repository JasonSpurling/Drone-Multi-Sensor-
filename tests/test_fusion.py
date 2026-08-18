from datetime import datetime

from app.db import upsert_authorized_operator
from app.fusion import fuse_classification
from app.models import Classification, Detection, SensorType

BASE_TIME = datetime(2026, 1, 1, 12, 0, 0)


def make_detection(**overrides) -> Detection:
    defaults = dict(
        sensor_id="s1", sensor_type=SensorType.RADAR, timestamp=BASE_TIME, confidence=0.9
    )
    defaults.update(overrides)
    return Detection(**defaults)


def test_empty_history_is_unknown():
    assert fuse_classification([]) == Classification.UNKNOWN


def test_single_high_confidence_detection_drives_result():
    detections = [make_detection(sensor_type=SensorType.RADAR, confidence=0.9)]
    assert fuse_classification(detections) == Classification.DRONE


def test_adsb_outweighs_a_single_low_trust_camera_vote():
    detections = [
        make_detection(sensor_type=SensorType.ADSB, confidence=0.98),
        make_detection(sensor_type=SensorType.CAMERA, confidence=0.3),  # bird-ish vote
    ]
    assert fuse_classification(detections) == Classification.AIRCRAFT


def test_many_corroborating_high_trust_votes_beat_one_low_trust_dissent():
    detections = [make_detection(sensor_type=SensorType.RADAR, confidence=0.9) for _ in range(5)]
    detections.append(make_detection(sensor_type=SensorType.ACOUSTIC, confidence=0.3))
    assert fuse_classification(detections) == Classification.DRONE


def test_unknown_votes_do_not_dilute_evidence():
    # A mid-confidence, non-discriminating reading contributes nothing.
    detections = [
        make_detection(sensor_type=SensorType.RADAR, confidence=0.9),
        make_detection(sensor_type=SensorType.CAMERA, confidence=0.5),  # UNKNOWN, no vote
    ]
    assert fuse_classification(detections) == Classification.DRONE


def test_authorized_operator_id_votes_friendly():
    upsert_authorized_operator("OP-12345", name="Test Operator")
    detections = [
        make_detection(
            sensor_type=SensorType.RADAR, confidence=0.9, raw_data={"operator_id": "OP-12345"}
        )
    ]
    assert fuse_classification(detections) == Classification.FRIENDLY


def test_unregistered_operator_id_does_not_grant_friendly():
    detections = [
        make_detection(
            sensor_type=SensorType.RADAR, confidence=0.9, raw_data={"operator_id": "OP-UNKNOWN"}
        )
    ]
    assert fuse_classification(detections) == Classification.DRONE
