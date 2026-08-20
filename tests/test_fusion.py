from datetime import datetime

from app.db import upsert_authorized_operator
from app.fusion import fuse_classification
from app.models import Classification, Detection, SensorType
from app.remote_id import generate_keypair, sign_detection

BASE_TIME = datetime(2026, 1, 1, 12, 0, 0)


def make_detection(**overrides) -> Detection:
    defaults = {
        "sensor_id": "s1", "sensor_type": SensorType.RADAR, "timestamp": BASE_TIME, "confidence": 0.9
    }
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


def test_rf_signature_match_boosts_a_low_confidence_detection_to_drone():
    # Sensor itself reports low confidence, but the RF envelope matches a
    # known drone-control-link signature (DJI OcuSync band/bandwidth/
    # hopping) -- that signal-shape evidence should be enough to classify
    # DRONE even though the sensor's own confidence alone wouldn't cross
    # the threshold.
    detection = make_detection(
        sensor_type=SensorType.RF,
        confidence=0.3,
        raw_data={"center_frequency_mhz": 2440.0, "bandwidth_mhz": 10.0, "frequency_hopping": True},
    )
    assert fuse_classification([detection]) == Classification.DRONE


def test_rf_with_no_signature_match_falls_back_to_raw_confidence():
    detection = make_detection(
        sensor_type=SensorType.RF,
        confidence=0.3,
        raw_data={"center_frequency_mhz": 900.0, "bandwidth_mhz": 10.0},
    )
    assert fuse_classification([detection]) == Classification.UNKNOWN


def test_rf_without_raw_data_behaves_exactly_as_before():
    detection = make_detection(sensor_type=SensorType.RF, confidence=0.9)
    assert fuse_classification([detection]) == Classification.DRONE


def test_correctly_signed_authorized_operator_votes_friendly():
    private_key, public_key = generate_keypair()
    upsert_authorized_operator("OP-12345", name="Test Operator", public_key=public_key)
    detection = make_detection(sensor_type=SensorType.RADAR, confidence=0.9, raw_data={"operator_id": "OP-12345"})
    signature = sign_detection("OP-12345", detection, private_key)
    detection.raw_data["signature"] = signature
    assert fuse_classification([detection]) == Classification.FRIENDLY


def test_unregistered_operator_id_does_not_grant_friendly():
    detections = [
        make_detection(
            sensor_type=SensorType.RADAR, confidence=0.9, raw_data={"operator_id": "OP-UNKNOWN"}
        )
    ]
    assert fuse_classification(detections) == Classification.DRONE


def test_spoofed_operator_id_without_signature_does_not_grant_friendly():
    # Regression test for the security-review finding: registering an
    # operator_id used to be enough to trust *any* detection claiming it.
    # Now a bare operator_id claim, with no valid signature, must not vote
    # FRIENDLY -- an attacker who only knows/guesses a valid operator_id
    # (e.g. from having ingest access) gains nothing.
    _, public_key = generate_keypair()
    upsert_authorized_operator("OP-12345", name="Test Operator", public_key=public_key)
    detections = [
        make_detection(
            sensor_type=SensorType.RADAR, confidence=0.9, raw_data={"operator_id": "OP-12345"}
        )
    ]
    assert fuse_classification(detections) == Classification.DRONE


def test_spoofed_operator_id_with_wrong_signature_does_not_grant_friendly():
    _private_key_a, public_key_a = generate_keypair()
    private_key_b, _ = generate_keypair()
    upsert_authorized_operator("OP-12345", name="Test Operator", public_key=public_key_a)

    detection = make_detection(sensor_type=SensorType.RADAR, confidence=0.9, raw_data={"operator_id": "OP-12345"})
    # Signed with a *different* private key than the one registered for OP-12345.
    forged_signature = sign_detection("OP-12345", detection, private_key_b)
    detection.raw_data["signature"] = forged_signature
    assert fuse_classification([detection]) == Classification.DRONE


def test_signature_cannot_be_replayed_onto_a_different_detection():
    private_key, public_key = generate_keypair()
    upsert_authorized_operator("OP-12345", name="Test Operator", public_key=public_key)

    original = make_detection(sensor_type=SensorType.RADAR, confidence=0.9, raw_data={"operator_id": "OP-12345"})
    signature = sign_detection("OP-12345", original, private_key)

    # Same signature, different detection (different position) -- must not verify.
    replayed = make_detection(
        sensor_type=SensorType.RADAR, confidence=0.9, latitude=1.0, longitude=1.0,
        raw_data={"operator_id": "OP-12345", "signature": signature},
    )
    assert fuse_classification([replayed]) == Classification.DRONE
