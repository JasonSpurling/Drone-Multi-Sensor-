from app.ml.features import extract_features
from app.models import Detection, SensorType


def _detection(**overrides) -> Detection:
    defaults = {
        "sensor_id": "s1", "sensor_type": SensorType.CAMERA,
        "latitude": 51.5, "longitude": -0.1, "confidence": 0.8,
    }
    defaults.update(overrides)
    return Detection(**defaults)


def test_extracts_sensor_type_and_confidence():
    features = extract_features(_detection(sensor_type=SensorType.RADAR, confidence=0.6))
    assert features["sensor_type"] == "radar"
    assert features["confidence"] == 0.6


def test_omits_altitude_when_not_reported():
    features = extract_features(_detection(altitude_m=None))
    assert "altitude_m" not in features


def test_includes_altitude_when_reported():
    features = extract_features(_detection(altitude_m=120.0))
    assert features["altitude_m"] == 120.0


def test_omits_rf_features_for_a_non_rf_detection_even_with_raw_data_present():
    features = extract_features(
        _detection(sensor_type=SensorType.CAMERA, raw_data={"center_frequency_mhz": 2400.0})
    )
    assert "rf_center_frequency_mhz" not in features


def test_includes_rf_features_for_an_rf_detection():
    features = extract_features(
        _detection(
            sensor_type=SensorType.RF,
            raw_data={"center_frequency_mhz": 2412.0, "bandwidth_mhz": 20.0, "frequency_hopping": True},
        )
    )
    assert features["rf_center_frequency_mhz"] == 2412.0
    assert features["rf_bandwidth_mhz"] == 20.0
    assert features["rf_frequency_hopping"] == 1.0


def test_omits_rf_frequency_hopping_when_false_or_absent():
    features = extract_features(
        _detection(sensor_type=SensorType.RF, raw_data={"center_frequency_mhz": 2412.0, "frequency_hopping": False})
    )
    assert "rf_frequency_hopping" not in features


def test_omits_all_rf_features_when_raw_data_is_none():
    features = extract_features(_detection(sensor_type=SensorType.RF, raw_data=None))
    assert "rf_center_frequency_mhz" not in features
    assert "rf_bandwidth_mhz" not in features
    assert "rf_frequency_hopping" not in features


def test_includes_rf_signature_match_confidence_when_a_known_signature_matches():
    # 2440 MHz / 10 MHz bandwidth / hopping matches the same built-in
    # drone-control-link signature app/fusion.py's RF confidence boost
    # (test_fusion.py's test_rf_signature_match_boosts_...) relies on.
    features = extract_features(
        _detection(
            sensor_type=SensorType.RF,
            raw_data={"center_frequency_mhz": 2440.0, "bandwidth_mhz": 10.0, "frequency_hopping": True},
        )
    )
    assert features["rf_signature_match_confidence"] == 0.9


def test_omits_rf_signature_match_confidence_when_nothing_matches():
    features = extract_features(
        _detection(
            sensor_type=SensorType.RF,
            raw_data={"center_frequency_mhz": 900.0, "bandwidth_mhz": 10.0},
        )
    )
    assert "rf_signature_match_confidence" not in features


def test_omits_rf_signature_match_confidence_for_a_non_rf_detection():
    features = extract_features(
        _detection(sensor_type=SensorType.CAMERA, raw_data={"center_frequency_mhz": 2440.0, "bandwidth_mhz": 10.0})
    )
    assert "rf_signature_match_confidence" not in features
