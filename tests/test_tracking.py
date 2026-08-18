from datetime import datetime, timedelta

import pytest

from app.db import list_tracks
from app.models import Classification, Detection, SensorType, TrackStatus
from app.tracking import associate_detection, haversine_distance_m

BASE_TIME = datetime(2026, 1, 1, 12, 0, 0)


def make_detection(**overrides) -> Detection:
    defaults = dict(
        sensor_id="radar-1",
        sensor_type=SensorType.RADAR,
        timestamp=BASE_TIME,
        latitude=51.5,
        longitude=-0.1,
        altitude_m=100,
        confidence=0.9,
    )
    defaults.update(overrides)
    return Detection(**defaults)


def test_haversine_zero_for_identical_points():
    assert haversine_distance_m(51.5, -0.1, 51.5, -0.1) == pytest.approx(0.0)


def test_haversine_known_distance():
    # Roughly 1 degree of latitude ~= 111.32 km.
    distance = haversine_distance_m(51.0, 0.0, 52.0, 0.0)
    assert distance == pytest.approx(111_320, rel=0.01)


def test_first_detection_creates_new_track():
    detection = associate_detection(make_detection())
    tracks = list_tracks()
    assert len(tracks) == 1
    assert detection.track_id == tracks[0].id
    assert tracks[0].status == TrackStatus.ACTIVE


def test_nearby_detection_joins_existing_track():
    first = associate_detection(make_detection())
    second = associate_detection(
        make_detection(
            timestamp=BASE_TIME + timedelta(seconds=5),
            latitude=51.5001,
            longitude=-0.1001,
        )
    )
    assert second.track_id == first.track_id
    assert len(list_tracks()) == 1


def test_far_detection_creates_separate_track():
    first = associate_detection(make_detection())
    second = associate_detection(
        make_detection(
            timestamp=BASE_TIME + timedelta(seconds=5),
            latitude=52.5,
            longitude=1.5,
        )
    )
    assert second.track_id != first.track_id
    assert len(list_tracks()) == 2


def test_detection_outside_time_gate_creates_separate_track():
    first = associate_detection(make_detection())
    second = associate_detection(
        make_detection(timestamp=BASE_TIME + timedelta(seconds=60))
    )
    assert second.track_id != first.track_id
    assert len(list_tracks()) == 2


def test_track_upgrades_from_unknown_to_drone():
    associate_detection(make_detection(sensor_type=SensorType.RF, confidence=0.5))  # -> UNKNOWN
    associate_detection(
        make_detection(
            timestamp=BASE_TIME + timedelta(seconds=5),
            sensor_type=SensorType.RF,
            confidence=0.95,  # -> DRONE
        )
    )
    track = list_tracks()[0]
    assert track.classification == Classification.DRONE


def test_track_upgrades_from_bird_to_drone():
    # Regression test: a track first read as a low-confidence camera/acoustic
    # return (classified BIRD) must still be promoted to DRONE once later
    # detections clearly show a drone -- it must not get stuck at BIRD.
    associate_detection(make_detection(sensor_type=SensorType.CAMERA, confidence=0.1))  # -> BIRD
    associate_detection(
        make_detection(
            timestamp=BASE_TIME + timedelta(seconds=5),
            sensor_type=SensorType.CAMERA,
            confidence=0.95,  # -> DRONE
        )
    )
    track = list_tracks()[0]
    assert track.classification == Classification.DRONE


def test_confident_classification_does_not_decay_back_to_bird():
    associate_detection(make_detection(sensor_type=SensorType.CAMERA, confidence=0.95))  # -> DRONE
    associate_detection(
        make_detection(
            timestamp=BASE_TIME + timedelta(seconds=5),
            sensor_type=SensorType.CAMERA,
            confidence=0.1,  # would be BIRD on its own
        )
    )
    track = list_tracks()[0]
    assert track.classification == Classification.DRONE
