import json

import pytest

from app.airspace.faa_notam import fetch_notams, import_notams_as_zones, notams_to_zones
from app.db import get_zone_by_name, list_zones
from app.geo import haversine_distance_m
from app.models import ZoneType

SAMPLE_RESPONSE = {
    "items": [
        {
            "properties": {
                "coreNOTAMData": {
                    "notam": {
                        "number": "A1234/26",
                        "text": "UAS OPERATIONS PROHIBITED WI 1NM RADIUS",
                        "traditionalMessage": "!UAS A1234/26 ...",
                        "classification": "DOM",
                        "effectiveStart": "2026-08-01T00:00:00.000Z",
                        "effectiveEnd": "2026-09-01T00:00:00.000Z",
                        "icaoLocation": "EGLL",
                    }
                }
            }
        },
        {"properties": {}},  # malformed/unexpected entry -- must not crash the whole fetch
    ]
}


def test_fetch_notams_sends_credentials_as_headers(monkeypatch):
    captured = {}

    class _FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps(SAMPLE_RESPONSE).encode()

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["headers"] = {k.lower(): v for k, v in request.headers.items()}
        return _FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    notams = fetch_notams("my-client-id", "my-client-secret", lat=51.5, lon=-0.1, radius_nm=50)

    assert captured["headers"]["client_id"] == "my-client-id"
    assert captured["headers"]["client_secret"] == "my-client-secret"
    assert "locationLatitude=51.5" in captured["url"]
    assert "locationRadius=50" in captured["url"]
    assert "responseFormat=geoJson" in captured["url"]

    assert len(notams) == 1
    assert notams[0]["number"] == "A1234/26"
    assert notams[0]["icao_location"] == "EGLL"


def test_malformed_items_are_skipped_not_fatal(monkeypatch):
    class _FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps(SAMPLE_RESPONSE).encode()

    monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout=None: _FakeResponse())

    notams = fetch_notams("id", "secret", lat=51.5, lon=-0.1)
    assert len(notams) == 1  # the malformed second item didn't crash or appear


def test_empty_items_returns_empty_list(monkeypatch):
    class _FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps({"items": []}).encode()

    monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout=None: _FakeResponse())
    assert fetch_notams("id", "secret", lat=51.5, lon=-0.1) == []


SAMPLE_NOTAMS = [
    {"number": "A1234/26", "icao_location": "EGLL", "text": "UAS OPERATIONS PROHIBITED"},
    {"number": "B5678/26", "icao_location": "EGLC", "text": "TEMPORARY FLIGHT RESTRICTION"},
]


def test_notams_to_zones_builds_a_restricted_zone_per_notam():
    zones = notams_to_zones(SAMPLE_NOTAMS, center_lat=51.5, center_lon=-0.1, radius_nm=10)
    assert len(zones) == 2
    assert all(z.zone_type == ZoneType.RESTRICTED for z in zones)
    assert zones[0].name == "NOTAM A1234/26 (EGLL)"
    assert zones[1].name == "NOTAM B5678/26 (EGLC)"


def test_notams_to_zones_polygon_is_a_circle_of_the_given_radius():
    # An honest approximation: every vertex sits at the actual queried
    # radius from the actual queried center -- never a guess at the
    # NOTAM's own real (unknown) footprint. See app/airspace/faa_notam.py's
    # docstring for why that's the deliberate choice.
    radius_nm = 25.0
    expected_radius_m = radius_nm * 1852.0
    zones = notams_to_zones(SAMPLE_NOTAMS[:1], center_lat=51.5, center_lon=-0.1, radius_nm=radius_nm)
    polygon = zones[0].polygon
    assert len(polygon) == 16
    for lat, lon in polygon:
        distance_m = haversine_distance_m(51.5, -0.1, lat, lon)
        assert distance_m == pytest.approx(expected_radius_m, rel=0.01)


def test_notams_to_zones_every_notam_from_one_fetch_shares_the_same_circle():
    zones = notams_to_zones(SAMPLE_NOTAMS, center_lat=51.5, center_lon=-0.1, radius_nm=10)
    assert zones[0].polygon == zones[1].polygon


def test_notams_to_zones_empty_list_returns_empty_zones():
    assert notams_to_zones([], center_lat=51.5, center_lon=-0.1, radius_nm=10) == []


def test_import_notams_as_zones_persists_and_is_safe_to_rerun(monkeypatch, site_id):
    class _FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps(
                {
                    "items": [
                        {
                            "properties": {
                                "coreNOTAMData": {
                                    "notam": {
                                        "number": "A1234/26", "text": "UAS OPERATIONS PROHIBITED",
                                        "icaoLocation": "EGLL",
                                    }
                                }
                            }
                        }
                    ]
                }
            ).encode()

    monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout=None: _FakeResponse())

    first = import_notams_as_zones("id", "secret", lat=51.5, lon=-0.1, site_id=site_id, radius_nm=10)
    assert len(first) == 1
    assert first[0].site_id == site_id
    assert get_zone_by_name("NOTAM A1234/26 (EGLL)", site_id) is not None

    # Re-running (e.g. a periodic cron re-fetch) with the same still-active
    # NOTAM must not create a duplicate zone.
    second = import_notams_as_zones("id", "secret", lat=51.5, lon=-0.1, site_id=site_id, radius_nm=10)
    assert len(second) == 1
    assert len(list_zones(site_id=site_id)) == 1
