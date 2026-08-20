import math
from datetime import datetime, timedelta

import pytest

from app.db import list_tracks
from app.models import Classification, Detection, SensorType, TrackStatus
from app.tracking import associate_detection, haversine_distance_m

BASE_TIME = datetime(2026, 1, 1, 12, 0, 0)


def make_detection(**overrides) -> Detection:
    defaults = {
        "sensor_id": "radar-1",
        "sensor_type": SensorType.RADAR,
        "timestamp": BASE_TIME,
        "latitude": 51.5,
        "longitude": -0.1,
        "altitude_m": 100,
        "confidence": 0.9,
    }
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


def test_track_gains_velocity_and_heading_from_repeated_detections():
    # Move ~10 m/s due east (heading 90) between fixes, spaced 5s apart.
    lon_step = 0.00006  # ~5m east near this latitude, x2 per 5s tick below
    for i in range(6):
        associate_detection(
            make_detection(
                timestamp=BASE_TIME + timedelta(seconds=5 * i),
                latitude=51.5,
                longitude=-0.1 + lon_step * i,
            )
        )
    track = list_tracks()[0]
    assert track.speed_mps is not None and track.speed_mps > 0.5
    assert track.heading_deg is not None
    assert 45 < track.heading_deg < 135  # roughly eastward


def test_track_position_uncertainty_shrinks_with_more_detections():
    associate_detection(make_detection())
    first_track = list_tracks()[0]
    first_uncertainty = first_track.position_uncertainty_m

    for i in range(1, 5):
        associate_detection(
            make_detection(
                timestamp=BASE_TIME + timedelta(seconds=5 * i),
                latitude=51.5 + 0.00001 * i,
                longitude=-0.1,
            )
        )
    later_track = list_tracks()[0]
    assert later_track.position_uncertainty_m < first_uncertainty


def test_fast_mover_still_gates_onto_predicted_position():
    # A steadily-moving object several TRACK_DISTANCE_GATE_M past its last
    # raw fix should still join the same track, because gating is against
    # the Kalman-predicted position (which moves with it), not the stale
    # last-known position.
    associate_detection(make_detection(latitude=51.5, longitude=-0.10000))
    associate_detection(
        make_detection(timestamp=BASE_TIME + timedelta(seconds=5), latitude=51.5, longitude=-0.09850)
    )
    third = associate_detection(
        make_detection(timestamp=BASE_TIME + timedelta(seconds=10), latitude=51.5, longitude=-0.09700)
    )
    assert len(list_tracks()) == 1
    assert third.track_id == list_tracks()[0].id


def test_fast_aircraft_at_one_hertz_stays_on_one_track():
    # Regression test: a ~210 m/s aircraft (typical ADS-B ground speed)
    # reporting once per second must stay associated to a single track.
    # With too-tight an initial velocity prior, the filter's first predict
    # step assumes near-zero velocity, so the object's real next position
    # (~200m away after 1s) falls outside the gate before the filter has
    # had a second update to learn its actual velocity -- exactly the
    # warm-up failure this test guards against.
    lat, lon = 51.5, -0.3
    heading_deg, speed_mps = 200.0, 210.0
    track_ids = []
    for i in range(8):
        detection = associate_detection(
            make_detection(
                sensor_id="adsb-1",
                sensor_type=SensorType.ADSB,
                timestamp=BASE_TIME + timedelta(seconds=i),
                latitude=lat,
                longitude=lon,
                confidence=0.98,
            )
        )
        track_ids.append(detection.track_id)
        distance_m = speed_mps * 1.0
        d_lat = (distance_m * math.cos(math.radians(heading_deg))) / 111_320.0
        meters_per_degree_lon = 111_320.0 * math.cos(math.radians(lat))
        d_lon = (distance_m * math.sin(math.radians(heading_deg))) / meters_per_degree_lon
        lat += d_lat
        lon += d_lon

    assert len(set(track_ids)) == 1
    assert len(list_tracks()) == 1


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
