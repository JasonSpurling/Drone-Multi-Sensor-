import json

import pytest

from app.airspace.faa_uas_facility_map import (
    fetch_facility_map_geojson,
    geojson_to_zones,
    import_facility_map_zones,
)
from app.db import list_zones
from app.models import ZoneType

# Shape matches a real ArcGIS FeatureServer f=geojson response: a plain
# GeoJSON FeatureCollection, polygon coordinates in (lon, lat) order per
# RFC 7946, with the facility map's CEILING attribute (feet AGL) in
# properties.
SAMPLE_GEOJSON = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "properties": {"CEILING": 100},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[-0.5, 51.3], [-0.4, 51.3], [-0.4, 51.4], [-0.5, 51.4], [-0.5, 51.3]]],
            },
        },
        {
            "type": "Feature",
            "properties": {"CEILING": 0},
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
    # GeoJSON has (-0.5, 51.3) as (lon, lat); Zone.polygon is (lat, lon).
    assert zones[0].polygon[0] == (51.3, -0.5)


def test_ceiling_feet_converted_to_altitude_meters():
    zones = geojson_to_zones(SAMPLE_GEOJSON)
    assert zones[0].max_altitude_m == pytest.approx(100 * 0.3048)


def test_zero_ceiling_is_a_real_zero_not_missing():
    zones = geojson_to_zones(SAMPLE_GEOJSON)
    assert zones[1].max_altitude_m == 0.0


def test_missing_ceiling_field_yields_no_altitude_cap():
    geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {},
                "geometry": {"type": "Polygon", "coordinates": [[[-0.5, 51.3], [-0.4, 51.3], [-0.4, 51.4]]]},
            }
        ],
    }
    zones = geojson_to_zones(geojson)
    assert zones[0].max_altitude_m is None


def test_non_polygon_features_are_skipped():
    geojson = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature", "properties": {"CEILING": 100},
            "geometry": {"type": "Point", "coordinates": [-0.5, 51.3]},
        }],
    }
    assert geojson_to_zones(geojson) == []


def test_polygon_features_with_no_coordinate_rings_are_skipped():
    geojson = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature", "properties": {"CEILING": 100},
            "geometry": {"type": "Polygon", "coordinates": []},
        }],
    }
    assert geojson_to_zones(geojson) == []


def test_zones_import_as_monitoring_type_not_restricted():
    zones = geojson_to_zones(SAMPLE_GEOJSON)
    assert all(zone.zone_type == ZoneType.MONITORING for zone in zones)


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

    result = fetch_facility_map_geojson(-0.5, 51.3, 0.3, 51.7)
    assert result == SAMPLE_GEOJSON
    assert "geometry=-0.5%2C51.3%2C0.3%2C51.7" in captured_urls[0]
    assert "geometryType=esriGeometryEnvelope" in captured_urls[0]
    assert "f=geojson" in captured_urls[0]


def test_import_persists_zones_and_is_idempotent(site_id, monkeypatch):
    def fake_fetch(*args, **kwargs):
        return SAMPLE_GEOJSON

    monkeypatch.setattr("app.airspace.faa_uas_facility_map.fetch_facility_map_geojson", fake_fetch)

    first = import_facility_map_zones(-0.5, 51.3, 0.3, 51.7, site_id)
    assert len(first) == 2
    assert all(zone.id is not None for zone in first)

    second = import_facility_map_zones(-0.5, 51.3, 0.3, 51.7, site_id)
    assert len(second) == 2

    # Re-running the import must not create duplicate rows.
    all_zones = list_zones(site_id=site_id)
    facility_map_zones = [z for z in all_zones if z.name.startswith("FAA UAS Facility Map")]
    assert len(facility_map_zones) == 2
