import json

import pytest

from app.airspace.faa_special_use_airspace import (
    classify_sua_type,
    fetch_special_use_airspace_geojson,
    geojson_to_zones,
    import_special_use_airspace_zones,
)
from app.db import list_zones
from app.models import ZoneType

SAMPLE_GEOJSON = {
    "type": "FeatureCollection",
    "features": [
        {
            # A Prohibited area -- a real "P-" designator, surface to 18000ft.
            "type": "Feature",
            "properties": {"TYPE": "PROHIBITED", "NAME": "P-56", "LOWER_ALT": "SFC", "UPPER_ALT": "18000"},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[-0.5, 51.3], [-0.4, 51.3], [-0.4, 51.4], [-0.5, 51.4], [-0.5, 51.3]]],
            },
        },
        {
            # A Warning area with an unlimited ceiling.
            "type": "Feature",
            "properties": {"TYPE": "WARNING", "NAME": "W-237A", "LOWER_ALT": "SFC", "UPPER_ALT": "UNL"},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[-0.4, 51.3], [-0.3, 51.3], [-0.3, 51.4], [-0.4, 51.4], [-0.4, 51.3]]],
            },
        },
    ],
}


def test_classify_prohibited_prefix_is_no_fly():
    assert classify_sua_type("P-56", None) == ZoneType.NO_FLY


def test_classify_restricted_prefix_is_no_fly():
    assert classify_sua_type("R-4401", None) == ZoneType.NO_FLY


def test_classify_warning_prefix_is_monitoring():
    assert classify_sua_type("W-237A", None) == ZoneType.MONITORING


def test_classify_alert_prefix_is_monitoring():
    assert classify_sua_type("A-211", None) == ZoneType.MONITORING


def test_classify_falls_back_to_type_field_when_name_has_no_recognized_prefix():
    assert classify_sua_type("Some MOA", "Restricted Area") == ZoneType.NO_FLY


def test_classify_defaults_to_monitoring_when_ambiguous():
    assert classify_sua_type(None, None) == ZoneType.MONITORING
    assert classify_sua_type("Bravo MOA", "Military Operations Area") == ZoneType.MONITORING


def test_classify_name_prefix_wins_over_a_misleading_type_field():
    # NAME's designator convention is the more reliable signal (see module
    # docstring) -- a "P-" prefix must win even if TYPE says otherwise.
    assert classify_sua_type("P-40", "Something else entirely") == ZoneType.NO_FLY


def test_geojson_polygon_coordinates_are_swapped_to_lat_lon():
    zones = geojson_to_zones(SAMPLE_GEOJSON)
    assert len(zones) == 2
    assert zones[0].polygon[0] == (51.3, -0.5)


def test_prohibited_area_imports_as_no_fly():
    zones = geojson_to_zones(SAMPLE_GEOJSON)
    assert zones[0].zone_type == ZoneType.NO_FLY
    assert "P-56" in zones[0].name


def test_warning_area_imports_as_monitoring():
    zones = geojson_to_zones(SAMPLE_GEOJSON)
    assert zones[1].zone_type == ZoneType.MONITORING


def test_sfc_floor_is_a_real_zero():
    zones = geojson_to_zones(SAMPLE_GEOJSON)
    assert zones[0].min_altitude_m == 0.0


def test_numeric_string_ceiling_converted_to_meters():
    zones = geojson_to_zones(SAMPLE_GEOJSON)
    assert zones[0].max_altitude_m == pytest.approx(18000 * 0.3048)


def test_unlimited_ceiling_is_none_not_a_fabricated_cap():
    zones = geojson_to_zones(SAMPLE_GEOJSON)
    assert zones[1].max_altitude_m is None


def test_non_polygon_features_are_skipped():
    geojson = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature", "properties": {"TYPE": "PROHIBITED", "NAME": "P-1"},
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

    result = fetch_special_use_airspace_geojson(-0.5, 51.3, 0.3, 51.7)
    assert result == SAMPLE_GEOJSON
    assert "geometry=-0.5%2C51.3%2C0.3%2C51.7" in captured_urls[0]
    assert "geometryType=esriGeometryEnvelope" in captured_urls[0]
    assert "f=geojson" in captured_urls[0]


def test_import_persists_zones_and_is_idempotent(site_id, monkeypatch):
    def fake_fetch(*args, **kwargs):
        return SAMPLE_GEOJSON

    monkeypatch.setattr("app.airspace.faa_special_use_airspace.fetch_special_use_airspace_geojson", fake_fetch)

    first = import_special_use_airspace_zones(-0.5, 51.3, 0.3, 51.7, site_id)
    assert len(first) == 2
    assert all(zone.id is not None for zone in first)

    second = import_special_use_airspace_zones(-0.5, 51.3, 0.3, 51.7, site_id)
    assert len(second) == 2

    all_zones = list_zones(site_id=site_id)
    sua_zones = [z for z in all_zones if z.name.startswith("FAA SUA")]
    assert len(sua_zones) == 2
