from app.classification import classify
from app.models import Classification, SensorType


def test_adsb_always_aircraft_regardless_of_confidence():
    assert classify(SensorType.ADSB, 0.01) == Classification.AIRCRAFT
    assert classify(SensorType.ADSB, 1.0) == Classification.AIRCRAFT


def test_high_confidence_is_drone_for_any_sensor():
    assert classify(SensorType.RADAR, 0.75) == Classification.DRONE
    assert classify(SensorType.CAMERA, 0.9) == Classification.DRONE
    assert classify(SensorType.RF, 0.99) == Classification.DRONE


def test_low_confidence_camera_or_acoustic_is_bird():
    assert classify(SensorType.CAMERA, 0.39) == Classification.BIRD
    assert classify(SensorType.ACOUSTIC, 0.0) == Classification.BIRD


def test_low_confidence_radar_is_unknown_not_bird():
    # Bird misclassification is only sensible for camera/acoustic returns.
    assert classify(SensorType.RADAR, 0.1) == Classification.UNKNOWN


def test_mid_confidence_is_unknown():
    assert classify(SensorType.CAMERA, 0.5) == Classification.UNKNOWN
