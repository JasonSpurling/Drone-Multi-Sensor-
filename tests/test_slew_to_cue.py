import pytest

from app.geo import destination_point
from app.slew_to_cue import compute_camera_cue

CAMERA_LAT, CAMERA_LON, CAMERA_ALT = 51.5, -0.1, 10.0


def test_pan_matches_bearing_to_a_due_north_target():
    target_lat, target_lon = destination_point(CAMERA_LAT, CAMERA_LON, bearing_deg=0.0, distance_m=1000.0)
    cue = compute_camera_cue(CAMERA_LAT, CAMERA_LON, CAMERA_ALT, 0.0, target_lat, target_lon, CAMERA_ALT)
    assert cue.pan_deg == pytest.approx(0.0, abs=1e-3)


def test_pan_matches_bearing_to_a_due_east_target():
    target_lat, target_lon = destination_point(CAMERA_LAT, CAMERA_LON, bearing_deg=90.0, distance_m=1000.0)
    cue = compute_camera_cue(CAMERA_LAT, CAMERA_LON, CAMERA_ALT, 0.0, target_lat, target_lon, CAMERA_ALT)
    assert cue.pan_deg == pytest.approx(90.0, abs=1e-3)


def test_tilt_is_zero_for_a_same_altitude_target():
    target_lat, target_lon = destination_point(CAMERA_LAT, CAMERA_LON, bearing_deg=45.0, distance_m=2000.0)
    cue = compute_camera_cue(CAMERA_LAT, CAMERA_LON, CAMERA_ALT, 0.0, target_lat, target_lon, CAMERA_ALT)
    assert cue.tilt_deg == pytest.approx(0.0, abs=1e-6)


def test_tilt_is_45_degrees_when_height_equals_horizontal_distance():
    target_lat, target_lon = destination_point(CAMERA_LAT, CAMERA_LON, bearing_deg=0.0, distance_m=1000.0)
    cue = compute_camera_cue(
        CAMERA_LAT, CAMERA_LON, CAMERA_ALT, 0.0, target_lat, target_lon, target_alt_m=CAMERA_ALT + 1000.0
    )
    assert cue.tilt_deg == pytest.approx(45.0, abs=0.1)


def test_tilt_is_negative_for_a_target_below_the_camera():
    target_lat, target_lon = destination_point(CAMERA_LAT, CAMERA_LON, bearing_deg=0.0, distance_m=1000.0)
    cue = compute_camera_cue(
        CAMERA_LAT, CAMERA_LON, CAMERA_ALT, 0.0, target_lat, target_lon, target_alt_m=CAMERA_ALT - 1000.0
    )
    assert cue.tilt_deg < 0


def test_missing_target_altitude_defaults_tilt_to_zero_rather_than_guessing():
    target_lat, target_lon = destination_point(CAMERA_LAT, CAMERA_LON, bearing_deg=30.0, distance_m=1500.0)
    cue = compute_camera_cue(CAMERA_LAT, CAMERA_LON, CAMERA_ALT, 0.0, target_lat, target_lon, target_alt_m=None)
    assert cue.tilt_deg == 0.0


def test_pan_relative_deg_accounts_for_camera_mounting_orientation():
    # Camera boresight points due east (90); a target also due east should
    # need zero relative pan -- it's dead ahead.
    target_lat, target_lon = destination_point(CAMERA_LAT, CAMERA_LON, bearing_deg=90.0, distance_m=1000.0)
    cue = compute_camera_cue(
        CAMERA_LAT, CAMERA_LON, CAMERA_ALT, camera_azimuth_reference_deg=90.0,
        target_lat=target_lat, target_lon=target_lon, target_alt_m=CAMERA_ALT,
    )
    assert cue.pan_relative_deg == pytest.approx(0.0, abs=1e-3)


def test_pan_relative_deg_wraps_correctly_around_360():
    # Camera boresight at 350 degrees; target at compass bearing 10 -- 20
    # degrees of relative pan, not -340.
    target_lat, target_lon = destination_point(CAMERA_LAT, CAMERA_LON, bearing_deg=10.0, distance_m=1000.0)
    cue = compute_camera_cue(
        CAMERA_LAT, CAMERA_LON, CAMERA_ALT, camera_azimuth_reference_deg=350.0,
        target_lat=target_lat, target_lon=target_lon, target_alt_m=CAMERA_ALT,
    )
    assert cue.pan_relative_deg == pytest.approx(20.0, abs=1e-3)


def test_distance_matches_haversine():
    from app.geo import haversine_distance_m

    target_lat, target_lon = destination_point(CAMERA_LAT, CAMERA_LON, bearing_deg=200.0, distance_m=3000.0)
    cue = compute_camera_cue(CAMERA_LAT, CAMERA_LON, CAMERA_ALT, 0.0, target_lat, target_lon, CAMERA_ALT)
    expected = haversine_distance_m(CAMERA_LAT, CAMERA_LON, target_lat, target_lon)
    assert cue.distance_m == pytest.approx(expected, rel=1e-9)


def test_camera_and_target_at_same_position_does_not_crash():
    cue = compute_camera_cue(CAMERA_LAT, CAMERA_LON, CAMERA_ALT, 0.0, CAMERA_LAT, CAMERA_LON, CAMERA_ALT)
    assert cue.distance_m == pytest.approx(0.0, abs=1e-6)
    assert cue.tilt_deg == 0.0
