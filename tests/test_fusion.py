from datetime import datetime, timedelta

import pytest

from app.config import CLASSIFICATION_CONFIDENCE_DECAY_SECONDS, CLASSIFICATION_CONFIDENCE_FLOOR
from app.db import upsert_authorized_operator
from app.fusion import classification_confidence, decay_classification_confidence, fuse_classification
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


def test_correctly_signed_authorized_operator_votes_friendly(site_id):
    private_key, public_key = generate_keypair()
    upsert_authorized_operator("OP-12345", site_id, name="Test Operator", public_key=public_key)
    detection = make_detection(
        site_id=site_id, sensor_type=SensorType.RADAR, confidence=0.9, raw_data={"operator_id": "OP-12345"}
    )
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


def test_spoofed_operator_id_without_signature_does_not_grant_friendly(site_id):
    # Regression test for the security-review finding: registering an
    # operator_id used to be enough to trust *any* detection claiming it.
    # Now a bare operator_id claim, with no valid signature, must not vote
    # FRIENDLY -- an attacker who only knows/guesses a valid operator_id
    # (e.g. from having ingest access) gains nothing.
    _, public_key = generate_keypair()
    upsert_authorized_operator("OP-12345", site_id, name="Test Operator", public_key=public_key)
    detections = [
        make_detection(
            site_id=site_id, sensor_type=SensorType.RADAR, confidence=0.9,
            raw_data={"operator_id": "OP-12345"},
        )
    ]
    assert fuse_classification(detections) == Classification.DRONE


def test_spoofed_operator_id_with_wrong_signature_does_not_grant_friendly(site_id):
    _private_key_a, public_key_a = generate_keypair()
    private_key_b, _ = generate_keypair()
    upsert_authorized_operator("OP-12345", site_id, name="Test Operator", public_key=public_key_a)

    detection = make_detection(
        site_id=site_id, sensor_type=SensorType.RADAR, confidence=0.9,
        raw_data={"operator_id": "OP-12345"},
    )
    # Signed with a *different* private key than the one registered for OP-12345.
    forged_signature = sign_detection("OP-12345", detection, private_key_b)
    detection.raw_data["signature"] = forged_signature
    assert fuse_classification([detection]) == Classification.DRONE


def test_signature_cannot_be_replayed_onto_a_different_detection(site_id):
    private_key, public_key = generate_keypair()
    upsert_authorized_operator("OP-12345", site_id, name="Test Operator", public_key=public_key)

    original = make_detection(
        site_id=site_id, sensor_type=SensorType.RADAR, confidence=0.9,
        raw_data={"operator_id": "OP-12345"},
    )
    signature = sign_detection("OP-12345", original, private_key)

    # Same signature, different detection (different position) -- must not verify.
    replayed = make_detection(
        site_id=site_id, sensor_type=SensorType.RADAR, confidence=0.9, latitude=1.0, longitude=1.0,
        raw_data={"operator_id": "OP-12345", "signature": signature},
    )
    assert fuse_classification([replayed]) == Classification.DRONE


def test_ml_model_is_consulted_ahead_of_the_rule_based_classifier_when_configured(monkeypatch):
    # A rule-based confidence of 0.9 would normally classify as DRONE
    # (see app.classification.classify) -- an ML "opinion" of BIRD here
    # proves fuse_classification actually asks app.ml.model.predict()
    # first, rather than ever falling through to the rule for a detection
    # the model has an opinion on.
    monkeypatch.setattr("app.fusion.ml_predict", lambda detection: Classification.BIRD)
    detections = [make_detection(sensor_type=SensorType.RADAR, confidence=0.9)]
    assert fuse_classification(detections) == Classification.BIRD


def test_ml_model_returning_none_falls_back_to_the_rule_based_classifier(monkeypatch):
    monkeypatch.setattr("app.fusion.ml_predict", lambda detection: None)
    detections = [make_detection(sensor_type=SensorType.RADAR, confidence=0.9)]
    assert fuse_classification(detections) == Classification.DRONE  # unchanged rule-based behavior


def test_authorized_operator_still_wins_over_an_ml_opinion(site_id, monkeypatch):
    # FRIENDLY (from a verified signature) is checked before the ML model
    # is even consulted -- an ML model has no way to know about a
    # cryptographically verified allowlist entry, so it must never
    # override one. A configured model opinionated for DRONE proves this
    # isn't just "no model configured" masking the real precedence.
    monkeypatch.setattr("app.fusion.ml_predict", lambda detection: Classification.DRONE)
    private_key, public_key = generate_keypair()
    upsert_authorized_operator("OP-99999", site_id, name="Test Operator", public_key=public_key)
    detection = make_detection(site_id=site_id, sensor_type=SensorType.RADAR, confidence=0.9)
    detection.raw_data = {"operator_id": "OP-99999"}
    signature = sign_detection("OP-99999", detection, private_key)
    detection.raw_data["signature"] = signature

    assert fuse_classification([detection]) == Classification.FRIENDLY


def test_classification_confidence_is_full_when_all_evidence_agrees():
    detections = [make_detection(sensor_type=SensorType.RADAR, confidence=0.9) for _ in range(3)]
    assert classification_confidence(detections, Classification.DRONE) == 1.0


def test_classification_confidence_reflects_a_specific_label_not_just_the_winner():
    # Mostly DRONE evidence with one dissenting AIRCRAFT vote (ADS-B, high
    # trust) -- confidence in DRONE specifically should be well under 1.0,
    # and confidence in the *losing* label (AIRCRAFT) should still be a
    # real, nonzero number, not just discarded because it didn't win.
    detections = [
        make_detection(sensor_type=SensorType.RADAR, confidence=0.9),
        make_detection(sensor_type=SensorType.RADAR, confidence=0.9),
        make_detection(sensor_type=SensorType.RADAR, confidence=0.9),
        make_detection(sensor_type=SensorType.ADSB, confidence=0.95),
    ]
    assert fuse_classification(detections) == Classification.DRONE  # accumulated radar weight still wins
    drone_confidence = classification_confidence(detections, Classification.DRONE)
    aircraft_confidence = classification_confidence(detections, Classification.AIRCRAFT)
    assert 0.0 < drone_confidence < 1.0
    assert 0.0 < aircraft_confidence < 1.0
    assert drone_confidence + aircraft_confidence == 1.0


def test_classification_confidence_is_none_for_unknown():
    assert classification_confidence([], Classification.UNKNOWN) is None
    detections = [make_detection(sensor_type=SensorType.CAMERA, confidence=0.5)]  # UNKNOWN, no vote
    assert classification_confidence(detections, Classification.UNKNOWN) is None


def test_decay_classification_confidence_is_unchanged_at_zero_age():
    now = datetime(2026, 1, 1, 12, 0, 0)
    assert decay_classification_confidence(0.9, now, now) == 0.9


def test_decay_classification_confidence_reaches_the_floor_at_the_configured_window():
    now = datetime(2026, 1, 1, 12, 0, 0)
    stale = now + timedelta(seconds=CLASSIFICATION_CONFIDENCE_DECAY_SECONDS)
    assert decay_classification_confidence(0.9, now, stale) == pytest.approx(CLASSIFICATION_CONFIDENCE_FLOOR)


def test_decay_classification_confidence_never_overshoots_the_floor():
    now = datetime(2026, 1, 1, 12, 0, 0)
    way_past = now + timedelta(seconds=CLASSIFICATION_CONFIDENCE_DECAY_SECONDS * 10)
    assert decay_classification_confidence(0.9, now, way_past) == pytest.approx(CLASSIFICATION_CONFIDENCE_FLOOR)


def test_decay_classification_confidence_is_monotonic_with_age():
    now = datetime(2026, 1, 1, 12, 0, 0)
    half_stale = now + timedelta(seconds=CLASSIFICATION_CONFIDENCE_DECAY_SECONDS / 2)
    fully_stale = now + timedelta(seconds=CLASSIFICATION_CONFIDENCE_DECAY_SECONDS)
    fresh_value = decay_classification_confidence(0.9, now, now)
    half_value = decay_classification_confidence(0.9, now, half_stale)
    stale_value = decay_classification_confidence(0.9, now, fully_stale)
    assert fresh_value > half_value > stale_value


def test_decay_classification_confidence_passes_through_none():
    assert decay_classification_confidence(None, datetime(2026, 1, 1), datetime(2026, 1, 2)) is None
