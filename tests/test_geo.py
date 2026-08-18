import pytest

from app.geo import haversine_distance_m, latlon_to_local_m, local_m_to_latlon


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
