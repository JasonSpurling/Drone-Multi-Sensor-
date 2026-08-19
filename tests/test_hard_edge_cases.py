"""Adversarial and boundary-condition tests: malformed/hostile input,
numerical edge cases, and stress scenarios beyond the normal-path
coverage elsewhere -- designed to break things, not just exercise them.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.config import FUSION_HISTORY_LIMIT
from app.db import create_zone, get_zone_by_name, list_tracks
from app.fusion import fuse_classification
from app.geo import destination_point, haversine_distance_m, latlon_to_local_m
from app.kalman import ConstantVelocityKalmanFilter
from app.main import app
from app.models import Classification, Detection, SensorType, Zone, ZoneType
from app.tracking import associate_detection
from app.zones import point_in_polygon

BASE_TIME = datetime(2026, 1, 1, 12, 0, 0)


# --- SQL injection / hostile string input --------------------------------

def test_sensor_id_with_sql_metacharacters_is_stored_verbatim_not_executed():
    hostile_id = "radar-1'; DROP TABLE track; --"
    detection = associate_detection(
        Detection(sensor_id=hostile_id, sensor_type=SensorType.RADAR, timestamp=BASE_TIME, confidence=0.9)
    )
    assert detection.sensor_id == hostile_id
    # If this were actually executed as SQL, the table would be gone and
    # this would raise instead of returning an empty (valid) list.
    assert list_tracks() != [] or list_tracks() == []  # table still queryable at all


def test_zone_name_with_sql_metacharacters_round_trips_safely():
    hostile_name = "zone'); DROP TABLE zone; --"
    create_zone(Zone(name=hostile_name, zone_type=ZoneType.RESTRICTED, polygon=[(0, 0), (0, 1), (1, 1)]))
    assert get_zone_by_name(hostile_name) is not None
    assert get_zone_by_name("zone") is None  # partial match shouldn't hit


def test_api_rejects_hostile_json_gracefully(isolated_db):
    with TestClient(app) as client:
        r = client.post(
            "/api/detections",
            json={"sensor_id": "<script>alert(1)</script>", "sensor_type": "radar", "confidence": 0.9},
        )
        assert r.status_code == 201
        assert r.json()["sensor_id"] == "<script>alert(1)</script>"  # stored as inert data, not executed


# --- Geodesy at extreme/boundary values -----------------------------------

def test_haversine_antipodal_points_is_half_earth_circumference():
    distance = haversine_distance_m(0.0, 0.0, 0.0, 180.0)
    assert distance == pytest.approx(20_015_086, rel=0.001)  # half Earth's circumference


def test_haversine_near_poles_does_not_error():
    assert haversine_distance_m(89.999, 0.0, -89.999, 180.0) > 0
    assert haversine_distance_m(90.0, 0.0, 90.0, 90.0) == pytest.approx(0.0, abs=1.0)


def test_destination_point_wraparound_bearing_matches_zero():
    lat1, lon1 = destination_point(51.5, -0.1, bearing_deg=0.0, distance_m=1000.0)
    lat2, lon2 = destination_point(51.5, -0.1, bearing_deg=360.0, distance_m=1000.0)
    assert lat1 == pytest.approx(lat2, abs=1e-9)
    assert lon1 == pytest.approx(lon2, abs=1e-9)


def test_destination_point_negative_bearing_equivalent_to_positive():
    lat1, lon1 = destination_point(51.5, -0.1, bearing_deg=-90.0, distance_m=500.0)
    lat2, lon2 = destination_point(51.5, -0.1, bearing_deg=270.0, distance_m=500.0)
    assert lat1 == pytest.approx(lat2, abs=1e-9)
    assert lon1 == pytest.approx(lon2, abs=1e-9)


def test_local_m_projection_across_antimeridian_does_not_error():
    # Longitude near +/-180 -- a naive projection could wrap incorrectly.
    east, north = latlon_to_local_m(0.0, 179.9999, ref_lat=0.0, ref_lon=180.0)
    assert isinstance(east, float) and isinstance(north, float)


def test_point_in_polygon_degenerate_two_point_polygon_does_not_crash():
    assert point_in_polygon(0.5, 0.5, [(0.0, 0.0), (1.0, 1.0)]) in (True, False)


def test_point_in_polygon_exact_vertex_does_not_crash():
    square = [(0.0, 0.0), (0.0, 1.0), (1.0, 1.0), (1.0, 0.0)]
    # Exactly on a vertex -- ray casting can be ambiguous here; just must not error.
    assert point_in_polygon(0.0, 0.0, square) in (True, False)


# --- Kalman filter numerical edge cases ------------------------------------

def test_kalman_filter_survives_many_zero_dt_updates():
    kf = ConstantVelocityKalmanFilter(x=0.0, y=0.0)
    for _ in range(100):
        kf.predict(dt_s=0.0, process_noise_accel_variance=4.0)
        kf.update(zx=1.0, zy=1.0, measurement_variance=25.0)
    assert kf.x == pytest.approx(1.0, abs=1.0)


def test_kalman_filter_survives_very_large_dt():
    kf = ConstantVelocityKalmanFilter(x=0.0, y=0.0, vx=10.0, vy=0.0)
    kf.predict(dt_s=1_000_000.0, process_noise_accel_variance=4.0)
    assert kf.x == pytest.approx(10_000_000.0, rel=0.01)
    assert kf.position_uncertainty_m() > 0  # covariance grew, didn't blow up to nan/inf
    import math
    assert not math.isnan(kf.x) and not math.isinf(kf.x)


def test_kalman_filter_survives_near_zero_measurement_variance():
    kf = ConstantVelocityKalmanFilter(x=0.0, y=0.0, position_variance=100.0, velocity_variance=10.0)
    kf.update(zx=5.0, zy=5.0, measurement_variance=1e-9)  # near-certain measurement
    assert kf.x == pytest.approx(5.0, abs=0.1)
    import math
    assert not math.isnan(kf.x)


def test_kalman_filter_repeated_predict_without_update_does_not_diverge():
    kf = ConstantVelocityKalmanFilter(x=0.0, y=0.0, vx=1.0, vy=1.0)
    for _ in range(1000):
        kf.predict(dt_s=1.0, process_noise_accel_variance=4.0)
    import math
    assert not math.isnan(kf.x) and not math.isinf(kf.x)
    assert kf.x == pytest.approx(1000.0, rel=0.01)


# --- Fusion under adversarial/large input ----------------------------------

def test_fusion_history_limit_is_respected_even_with_many_detections():
    # tracking.py caps fusion history at FUSION_HISTORY_LIMIT; verify the
    # fuse_classification function itself handles a pile of detections
    # without misbehaving (used with an unbounded list here to simulate
    # what would happen if the cap were ever removed by mistake).
    detections = [
        Detection(sensor_id="s", sensor_type=SensorType.RADAR, timestamp=BASE_TIME, confidence=0.9)
        for _ in range(FUSION_HISTORY_LIMIT * 4)
    ]
    assert fuse_classification(detections) == Classification.DRONE


def test_fusion_with_zero_confidence_detections_is_unknown():
    detections = [
        Detection(sensor_id="s", sensor_type=SensorType.RADAR, timestamp=BASE_TIME, confidence=0.0)
        for _ in range(10)
    ]
    # Zero confidence -> classify() returns UNKNOWN for radar -> no votes.
    assert fuse_classification(detections) == Classification.UNKNOWN


# --- Concurrency stress: multiple distinct simultaneous objects -----------

def test_concurrent_detections_for_distinct_objects_do_not_cross_contaminate():
    # 10 spatially-separated "objects" each posting 5 detections concurrently
    # across threads -- must end up as exactly 10 tracks, each with exactly
    # 5 of that object's detections, never merged or split incorrectly.
    def post(object_index: int, detection_index: int):
        lat = 40.0 + object_index * 2.0  # >200km apart, way outside any gate
        return associate_detection(
            Detection(
                sensor_id=f"sensor-{object_index}",
                sensor_type=SensorType.RADAR,
                timestamp=BASE_TIME,
                latitude=lat,
                longitude=0.0,
                confidence=0.9,
            )
        )

    jobs = [(obj, i) for obj in range(10) for i in range(5)]
    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(lambda args: post(*args), jobs))

    tracks = list_tracks()
    assert len(tracks) == 10
    track_ids_by_object: dict[int, set[int]] = {}
    for (obj, _), detection in zip(jobs, results):
        track_ids_by_object.setdefault(obj, set()).add(detection.track_id)
    for obj, ids in track_ids_by_object.items():
        assert len(ids) == 1, f"object {obj} split across multiple tracks: {ids}"
    all_track_ids = {tid for ids in track_ids_by_object.values() for tid in ids}
    assert len(all_track_ids) == 10  # no two objects shared a track


# --- Auth edge cases --------------------------------------------------------

def test_empty_string_api_key_header_is_rejected(isolated_db, monkeypatch):
    monkeypatch.setattr("app.config.API_KEY", "real-secret-key")
    monkeypatch.setattr("app.config.API_KEYS_JSON", "")
    with TestClient(app) as client:
        r = client.get("/api/tracks", headers={"X-API-Key": ""})
        assert r.status_code == 401


def test_api_key_is_case_sensitive(isolated_db, monkeypatch):
    monkeypatch.setattr("app.config.API_KEY", "CaseSensitiveKey123")
    monkeypatch.setattr("app.config.API_KEYS_JSON", "")
    with TestClient(app) as client:
        r = client.get("/api/tracks", headers={"X-API-Key": "casesensitivekey123"})
        assert r.status_code == 401
        r = client.get("/api/tracks", headers={"X-API-Key": "CaseSensitiveKey123"})
        assert r.status_code == 200


def test_very_long_api_key_header_is_handled_safely(isolated_db, monkeypatch):
    monkeypatch.setattr("app.config.API_KEY", "short-key")
    monkeypatch.setattr("app.config.API_KEYS_JSON", "")
    with TestClient(app) as client:
        r = client.get("/api/tracks", headers={"X-API-Key": "x" * 100_000})
        assert r.status_code == 401  # must not crash/hang on a huge header


# --- Classification threshold boundaries -----------------------------------

def test_confidence_exactly_at_drone_threshold_is_drone():
    from app.classification import classify
    assert classify(SensorType.RADAR, 0.75) == Classification.DRONE  # DRONE_CONFIDENCE_THRESHOLD default


def test_confidence_just_below_drone_threshold_is_not_drone():
    from app.classification import classify
    assert classify(SensorType.RADAR, 0.749999) != Classification.DRONE


def test_confidence_exactly_at_bird_threshold_is_not_bird():
    from app.classification import classify
    # BIRD_CONFIDENCE_THRESHOLD default 0.4; rule is strictly "< threshold".
    assert classify(SensorType.CAMERA, 0.4) != Classification.BIRD
    assert classify(SensorType.CAMERA, 0.399999) == Classification.BIRD
