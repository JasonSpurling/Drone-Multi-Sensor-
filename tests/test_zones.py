import pytest
from sqlalchemy.exc import IntegrityError

from app.db import create_zone
from app.models import Zone, ZoneType
from app.zones import point_in_polygon, zones_containing_point

SQUARE = [(51.0, -0.1), (51.0, 0.1), (51.2, 0.1), (51.2, -0.1)]


def test_point_in_polygon_inside():
    assert point_in_polygon(51.1, 0.0, SQUARE) is True


def test_point_in_polygon_outside():
    assert point_in_polygon(52.0, 0.0, SQUARE) is False


def test_zones_containing_point_matches_by_geometry(site_id):
    create_zone(
        Zone(site_id=site_id, name="test-zone", zone_type=ZoneType.RESTRICTED, polygon=SQUARE)
    )
    inside = zones_containing_point(51.1, 0.0, site_id)
    outside = zones_containing_point(52.0, 0.0, site_id)
    assert [z.name for z in inside] == ["test-zone"]
    assert outside == []


def test_zones_containing_point_respects_altitude_band(site_id):
    create_zone(
        Zone(
            site_id=site_id,
            name="banded-zone",
            zone_type=ZoneType.RESTRICTED,
            polygon=SQUARE,
            min_altitude_m=50,
            max_altitude_m=150,
        )
    )
    assert [z.name for z in zones_containing_point(51.1, 0.0, site_id, altitude_m=100)] == ["banded-zone"]
    assert zones_containing_point(51.1, 0.0, site_id, altitude_m=10) == []
    assert zones_containing_point(51.1, 0.0, site_id, altitude_m=500) == []


def test_zones_containing_point_unknown_altitude_is_conservative(site_id):
    # A detection with no altitude reading must not be excluded from an
    # altitude-banded zone -- missing data shouldn't suppress a real incursion.
    create_zone(
        Zone(
            site_id=site_id,
            name="banded-zone",
            zone_type=ZoneType.RESTRICTED,
            polygon=SQUARE,
            min_altitude_m=50,
            max_altitude_m=150,
        )
    )
    assert [z.name for z in zones_containing_point(51.1, 0.0, site_id, altitude_m=None)] == ["banded-zone"]


def test_inactive_zones_excluded(site_id):
    create_zone(
        Zone(site_id=site_id, name="inactive-zone", zone_type=ZoneType.RESTRICTED, polygon=SQUARE, active=False)
    )
    assert zones_containing_point(51.1, 0.0, site_id) == []


def test_duplicate_zone_name_in_the_same_site_is_rejected_at_the_db_layer(site_id):
    # The DB-level guarantee (idx_zone_site_id_name, schema.py) behind
    # app.api.zones' own get_zone_by_name pre-check -- this proves the
    # constraint itself catches it, independent of that pre-check, for
    # the race where two concurrent requests both pass it before either
    # inserts.
    create_zone(Zone(site_id=site_id, name="dup", zone_type=ZoneType.RESTRICTED, polygon=SQUARE))
    with pytest.raises(IntegrityError):
        create_zone(Zone(site_id=site_id, name="dup", zone_type=ZoneType.RESTRICTED, polygon=SQUARE))


def test_same_zone_name_is_allowed_across_different_sites(site_id):
    from app.db import create_site

    other_site = create_site("other-site")
    create_zone(Zone(site_id=site_id, name="shared-name", zone_type=ZoneType.RESTRICTED, polygon=SQUARE))
    # Must not raise -- uniqueness is per-site, not global.
    create_zone(Zone(site_id=other_site.id, name="shared-name", zone_type=ZoneType.RESTRICTED, polygon=SQUARE))
