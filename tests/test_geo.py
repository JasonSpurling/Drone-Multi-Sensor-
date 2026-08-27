import pytest

from app.geo import (
    destination_point,
    haversine_distance_m,
    initial_bearing_deg,
    latlon_to_local_m,
    local_m_to_latlon,
)


def test_latlon_to_local_m_at_reference_is_origin():
    east, north = latlon_to_local_m(51.5, -0.1, ref_lat=51.5, ref_lon=-0.1)
    assert east == pytest.approx(0.0, abs=1e-6)
    assert north == pytest.approx(0.0, abs=1e-6)


def test_local_m_to_latlon_is_inverse_of_latlon_to_local_m():
    lat, lon = 51.51, -0.09
    ref_lat, ref_lon = 51.5, -0.1
    east, north = latlon_to_local_m(lat, lon, ref_lat, ref_lon)
    round_trip_lat, round_trip_lon = local_m_to_latlon(east, north, ref_lat, ref_lon)
    assert round_trip_lat == pytest.approx(lat, abs=1e-6)
    assert round_trip_lon == pytest.approx(lon, abs=1e-6)


def test_local_m_projection_matches_haversine_for_small_offsets():
    ref_lat, ref_lon = 51.5, -0.1
    lat, lon = 51.501, -0.099
    east, north = latlon_to_local_m(lat, lon, ref_lat, ref_lon)
    local_distance = (east**2 + north**2) ** 0.5
    great_circle_distance = haversine_distance_m(ref_lat, ref_lon, lat, lon)
    assert local_distance == pytest.approx(great_circle_distance, rel=0.01)


def test_destination_point_due_north_moves_only_latitude():
    lat, lon = destination_point(51.5, -0.1, bearing_deg=0.0, distance_m=1000.0)
    assert lon == pytest.approx(-0.1, abs=1e-6)
    assert lat > 51.5


def test_destination_point_due_east_moves_only_longitude():
    lat, lon = destination_point(51.5, -0.1, bearing_deg=90.0, distance_m=1000.0)
    assert lat == pytest.approx(51.5, abs=1e-6)
    assert lon > -0.1


def test_destination_point_distance_matches_haversine():
    lat, lon = destination_point(51.5, -0.1, bearing_deg=37.0, distance_m=5000.0)
    assert haversine_distance_m(51.5, -0.1, lat, lon) == pytest.approx(5000.0, rel=1e-6)


def test_destination_point_zero_distance_is_noop():
    lat, lon = destination_point(51.5, -0.1, bearing_deg=123.0, distance_m=0.0)
    assert lat == pytest.approx(51.5, abs=1e-9)
    assert lon == pytest.approx(-0.1, abs=1e-9)


def test_initial_bearing_due_north_is_zero():
    assert initial_bearing_deg(51.5, -0.1, 51.6, -0.1) == pytest.approx(0.0, abs=1e-6)


def test_initial_bearing_due_south_is_180():
    assert initial_bearing_deg(51.5, -0.1, 51.4, -0.1) == pytest.approx(180.0, abs=1e-6)


def test_initial_bearing_is_the_inverse_of_destination_point():
    for bearing in [0, 45, 90, 135, 180, 225, 270, 315]:
        lat2, lon2 = destination_point(51.5, -0.1, bearing, 5000.0)
        recovered = initial_bearing_deg(51.5, -0.1, lat2, lon2)
        assert recovered == pytest.approx(bearing, abs=1e-6)


def test_slant_range_equals_ground_range_when_no_height_difference():
    from app.geo import slant_range_to_ground_range_m

    assert slant_range_to_ground_range_m(1000.0, 0.0) == pytest.approx(1000.0)


def test_slant_range_to_ground_range_is_shorter_when_target_is_higher():
    from app.geo import slant_range_to_ground_range_m

    # 500m slant range, 150m higher than the radar -> classic 3-4-5-ish
    # right triangle: ground_range = sqrt(500^2 - 150^2).
    ground = slant_range_to_ground_range_m(500.0, 150.0)
    assert ground == pytest.approx((500.0**2 - 150.0**2) ** 0.5)
    assert ground < 500.0


def test_slant_range_to_ground_range_ignores_sign_of_height_difference():
    from app.geo import slant_range_to_ground_range_m

    above = slant_range_to_ground_range_m(500.0, 150.0)
    below = slant_range_to_ground_range_m(500.0, -150.0)
    assert above == pytest.approx(below)


def test_slant_range_to_ground_range_falls_back_when_geometrically_impossible():
    from app.geo import slant_range_to_ground_range_m

    # A height difference that can't fit inside the slant range at all
    # (bad/inconsistent altitude data) -- must not raise.
    assert slant_range_to_ground_range_m(100.0, 500.0) == pytest.approx(100.0)
    assert slant_range_to_ground_range_m(100.0, 100.0) == pytest.approx(100.0)
