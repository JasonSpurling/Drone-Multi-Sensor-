from app.db import create_zone
from app.models import Zone, ZoneType
from app.zones import point_in_polygon, zones_containing_point

SQUARE = [(51.0, -0.1), (51.0, 0.1), (51.2, 0.1), (51.2, -0.1)]


def test_point_in_polygon_inside():
    assert point_in_polygon(51.1, 0.0, SQUARE) is True


def test_point_in_polygon_outside():
    assert point_in_polygon(52.0, 0.0, SQUARE) is False


def test_zones_containing_point_matches_by_geometry():
    create_zone(
        Zone(name="test-zone", zone_type=ZoneType.RESTRICTED, polygon=SQUARE)
    )
    inside = zones_containing_point(51.1, 0.0)
    outside = zones_containing_point(52.0, 0.0)
    assert [z.name for z in inside] == ["test-zone"]
    assert outside == []


def test_zones_containing_point_respects_altitude_band():
    create_zone(
        Zone(
            name="banded-zone",
            zone_type=ZoneType.RESTRICTED,
            polygon=SQUARE,
            min_altitude_m=50,
            max_altitude_m=150,
        )
    )
    assert [z.name for z in zones_containing_point(51.1, 0.0, altitude_m=100)] == ["banded-zone"]
    assert zones_containing_point(51.1, 0.0, altitude_m=10) == []
    assert zones_containing_point(51.1, 0.0, altitude_m=500) == []


def test_zones_containing_point_unknown_altitude_is_conservative():
    # A detection with no altitude reading must not be excluded from an
    # altitude-banded zone -- missing data shouldn't suppress a real incursion.
    create_zone(
        Zone(
            name="banded-zone",
            zone_type=ZoneType.RESTRICTED,
            polygon=SQUARE,
            min_altitude_m=50,
            max_altitude_m=150,
        )
    )
    assert [z.name for z in zones_containing_point(51.1, 0.0, altitude_m=None)] == ["banded-zone"]


def test_inactive_zones_excluded():
    create_zone(
        Zone(name="inactive-zone", zone_type=ZoneType.RESTRICTED, polygon=SQUARE, active=False)
    )
    assert zones_containing_point(51.1, 0.0) == []
