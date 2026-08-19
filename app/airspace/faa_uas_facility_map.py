"""Imports real FAA UAS Facility Map data -- the actual published maximum
altitudes UAS operators may fly at near an airport without further
authorization (the LAANC pre-authorization grid) -- as zones, instead of
relying only on the hand-seeded single zone in app/zones.seed.json.

Source: FAA's public, unauthenticated ArcGIS FeatureServer
(FAA_UAS_FacilityMap_Data, published via the FAA UAS Data Delivery System,
https://udds-faa.opendata.arcgis.com/) -- a real government-published
dataset covering the whole US as a grid of polygons, each carrying a
CEILING attribute (integer, feet AGL): the maximum altitude Part 107
operations are pre-authorized to without an additional LAANC/waiver
request. Standard ArcGIS REST `query` semantics (bbox via
esriGeometryEnvelope, `f=geojson` for a plain GeoJSON FeatureCollection
response) -- no API key needed.

Each grid cell is imported as a `monitoring` zone (not `restricted`) with
max_altitude_m set from CEILING: exceeding a facility map ceiling isn't
automatically a real intrusion the way entering a no-fly zone is, it just
means real-world LAANC authorization would be needed -- tune your own
deployment's incident-severity mapping (app/incidents.py) if you want
facility-map zones treated more strictly than the default monitoring
severity.

This module was not validated against a live response from the endpoint
in the environment this was built in (outbound network access here is
restricted to a small allowlist that doesn't include arcgis.com) -- the
query mechanics follow the standard, well-documented ArcGIS REST API
contract and the CEILING field name is confirmed from the layer's public
metadata, but you should sanity-check the first real import against a
known airport's published facility map before relying on it.
"""

from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request

from app.db import create_zone, get_zone_by_name
from app.models import Zone, ZoneType

logger = logging.getLogger(__name__)

_FEET_TO_M = 0.3048

DEFAULT_FEATURE_SERVER_URL = (
    "https://services6.arcgis.com/ssFJjBXIUyZDrSYZ/arcgis/rest/services/"
    "FAA_UAS_FacilityMap_Data/FeatureServer/0/query"
)


def _query_url(min_lon: float, min_lat: float, max_lon: float, max_lat: float, feature_server_url: str) -> str:
    params = {
        "where": "1=1",
        "outFields": "CEILING",
        "geometry": f"{min_lon},{min_lat},{max_lon},{max_lat}",
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "outSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "f": "geojson",
    }
    return f"{feature_server_url}?{urllib.parse.urlencode(params)}"


def fetch_facility_map_geojson(
    min_lon: float, min_lat: float, max_lon: float, max_lat: float,
    feature_server_url: str = DEFAULT_FEATURE_SERVER_URL, timeout: float = 15.0,
) -> dict:
    """Query the FeatureServer for every facility-map grid cell intersecting
    the given (lon/lat) bounding box, as a GeoJSON FeatureCollection.
    """
    url = _query_url(min_lon, min_lat, max_lon, max_lat, feature_server_url)
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read())


def geojson_to_zones(geojson: dict, name_prefix: str = "FAA UAS Facility Map") -> list[Zone]:
    """Convert a GeoJSON FeatureCollection of facility-map grid cells into
    this app's Zone model. GeoJSON polygon coordinates are (lon, lat), the
    opposite order of Zone.polygon's (lat, lon) -- swapped here, not left
    for a caller to get backwards.
    """
    zones: list[Zone] = []
    for i, feature in enumerate(geojson.get("features", [])):
        geometry = feature.get("geometry") or {}
        if geometry.get("type") != "Polygon":
            continue
        rings = geometry.get("coordinates") or []
        if not rings or not rings[0]:
            continue
        polygon = [(lat, lon) for lon, lat in rings[0]]

        ceiling_ft = (feature.get("properties") or {}).get("CEILING")
        max_altitude_m = ceiling_ft * _FEET_TO_M if ceiling_ft is not None else None

        zones.append(
            Zone(
                name=f"{name_prefix} #{i}",
                zone_type=ZoneType.MONITORING,
                polygon=polygon,
                min_altitude_m=None,
                max_altitude_m=max_altitude_m,
                active=True,
            )
        )
    return zones


def import_facility_map_zones(
    min_lon: float, min_lat: float, max_lon: float, max_lat: float,
    feature_server_url: str = DEFAULT_FEATURE_SERVER_URL,
) -> list[Zone]:
    """Fetch and persist every facility-map grid cell in the given bounding
    box, skipping any zone name already in the database (matches
    app.zones.load_zones_from_file's safe-to-rerun behavior).
    """
    geojson = fetch_facility_map_geojson(min_lon, min_lat, max_lon, max_lat, feature_server_url)
    imported: list[Zone] = []
    for zone in geojson_to_zones(geojson):
        if get_zone_by_name(zone.name) is None:
            zone = create_zone(zone)
            logger.info(
                "Imported FAA UAS Facility Map zone '%s' (ceiling=%s)",
                zone.name,
                f"{zone.max_altitude_m:.0f}m" if zone.max_altitude_m is not None else "unrestricted",
            )
        imported.append(zone)
    return imported
