import json

import pytest

from app.airspace.faa_class_airspace import (
    fetch_class_airspace_geojson,
    geojson_to_zones,
    import_class_airspace_zones,
)
from app.db import list_zones
from app.models import ZoneType

# Shape matches a real ArcGIS FeatureServer f=geojson response: a plain
# GeoJSON FeatureCollection, polygon coordinates in (lon, lat) order per
# RFC 7946, with the Class Airspace fields (confirmed from the FAA's own
# published AIS Open Data Dictionary -- see the module docstring) in
# properties.
SAMPLE_GEOJSON = {
    "type": "FeatureCollection",
    "features": [
        {
            # A typical Class D surface area: SFC to 2500ft MSL.
            "type": "Feature",
            "properties": {
                "CLASS": "D", "LOCAL_TYPE": "CLASS_D", "NAME": "EXAMPLE MUNI", "IDENT": "KEXM",
                "LOWER_VAL": None, "LOWER_UOM": None, "LOWER_CODE": "SFC",
                "UPPER_VAL": 2500.0, "UPPER_UOM": "FT", "UPPER_CODE": "MSL",
            },
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[-0.5, 51.3], [-0.4, 51.3], [-0.4, 51.4], [-0.5, 51.4], [-0.5, 51.3]]],
            },
        },
        {
            # A Class E extension with no upper limit and a nonzero floor,
            # given in flight levels (hundreds of feet).
            "type": "Feature",
            "properties": {
                "CLASS": "E4", "LOCAL_TYPE": "CLASS_E4", "NAME": None, "IDENT": "KEXM",
                "LOWER_VAL": 70.0, "LOWER_UOM": "FL", "LOWER_CODE": "MSL",
                "UPPER_VAL": None, "UPPER_UOM": None, "UPPER_CODE": "UNLTD",
            },
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[-0.4, 51.3], [-0.3, 51.3], [-0.3, 51.4], [-0.4, 51.4], [-0.4, 51.3]]],
            },
        },
    ],
}


def test_geojson_polygon_coordinates_are_swapped_to_lat_lon():
    zones = geojson_to_zones(SAMPLE_GEOJSON)
    assert len(zones) == 2
    assert zones[0].polygon[0] == (51.3, -0.5)


def test_surface_floor_is_a_real_zero():
    zones = geojson_to_zones(SAMPLE_GEOJSON)
    assert zones[0].min_altitude_m == 0.0


def test_feet_msl_ceiling_converted_to_meters():
    zones = geojson_to_zones(SAMPLE_GEOJSON)
    assert zones[0].max_altitude_m == pytest.approx(2500 * 0.3048)


def test_unlimited_ceiling_is_none_not_a_fabricated_cap():
    zones = geojson_to_zones(SAMPLE_GEOJSON)
    assert zones[1].max_altitude_m is None


def test_flight_level_floor_converted_via_hundreds_of_feet():
    # FL070 = 7000 feet, not 7 feet.
    zones = geojson_to_zones(SAMPLE_GEOJSON)
    assert zones[1].min_altitude_m == pytest.approx(7000 * 0.3048)


def test_missing_altitude_value_with_no_code_is_none_not_a_fabricated_cap():
    # No LOWER_CODE (not UNLTD, not SFC) and no LOWER_VAL at all -- a
    # genuinely missing/unusable value, distinct from the explicit UNLTD
    # case above.
    geojson = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature", "properties": {"CLASS": "D", "NAME": "TEST"},
            "geometry": {"type": "Polygon", "coordinates": [[[-0.5, 51.3], [-0.4, 51.3], [-0.4, 51.4]]]},
        }],
    }
    zones = geojson_to_zones(geojson)
    assert zones[0].min_altitude_m is None


def test_polygon_features_with_no_coordinate_rings_are_skipped():
    geojson = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature", "properties": {"CLASS": "D"},
            "geometry": {"type": "Polygon", "coordinates": []},
        }],
    }
    assert geojson_to_zones(geojson) == []


def test_zone_name_includes_label_and_class():
    zones = geojson_to_zones(SAMPLE_GEOJSON)
    assert "EXAMPLE MUNI" in zones[0].name
    assert "Class D" in zones[0].name
    # No NAME on the second feature -- falls back to IDENT.
    assert "KEXM" in zones[1].name
    assert "Class E4" in zones[1].name


def test_zones_import_as_monitoring_type_not_restricted():
    zones = geojson_to_zones(SAMPLE_GEOJSON)
    assert all(zone.zone_type == ZoneType.MONITORING for zone in zones)


def test_non_polygon_features_are_skipped():
    geojson = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature", "properties": {"CLASS": "D"},
            "geometry": {"type": "Point", "coordinates": [-0.5, 51.3]},
        }],
    }
    assert geojson_to_zones(geojson) == []


def test_fetch_builds_bbox_query_and_parses_response(monkeypatch):
    captured_urls = []

    class _FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps(SAMPLE_GEOJSON).encode()

    def fake_urlopen(url, timeout=None):
        captured_urls.append(url)
        return _FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    result = fetch_class_airspace_geojson(-0.5, 51.3, 0.3, 51.7)
    assert result == SAMPLE_GEOJSON
    assert "geometry=-0.5%2C51.3%2C0.3%2C51.7" in captured_urls[0]
    assert "geometryType=esriGeometryEnvelope" in captured_urls[0]
    assert "f=geojson" in captured_urls[0]


def test_import_persists_zones_and_is_idempotent(site_id, monkeypatch):
    def fake_fetch(*args, **kwargs):
        return SAMPLE_GEOJSON

    monkeypatch.setattr("app.airspace.faa_class_airspace.fetch_class_airspace_geojson", fake_fetch)

    first = import_class_airspace_zones(-0.5, 51.3, 0.3, 51.7, site_id)
    assert len(first) == 2
    assert all(zone.id is not None for zone in first)

    second = import_class_airspace_zones(-0.5, 51.3, 0.3, 51.7, site_id)
    assert len(second) == 2

    # Re-running the import must not create duplicate rows.
    all_zones = list_zones(site_id=site_id)
    class_airspace_zones = [z for z in all_zones if z.name.startswith("FAA Class Airspace")]
    assert len(class_airspace_zones) == 2
