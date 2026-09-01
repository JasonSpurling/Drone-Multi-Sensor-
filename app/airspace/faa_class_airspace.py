"""Imports real FAA Class Airspace data -- the controlled-airspace surface
areas (Class B/C/D, and the tiered Class E2/E3/E4/E5 extensions) drawn on
every VFR sectional chart -- as zones, the same "real published FAA data,
not a hand-drawn guess" role app/airspace/faa_uas_facility_map.py already
fills for LAANC ceiling grids. Entering one of these without the
authorization/clearance its class requires is a real regulatory matter
distinct from either the UAS Facility Map's altitude ceilings (a specific
flight needing LAANC authorization) or a NOTAM's temporary restriction
(app/airspace/faa_notam.py) -- this is the FAA's own permanent airspace
structure.

Source: FAA's public, unauthenticated ArcGIS FeatureServer
(Class_Airspace, published via FAA's AIS Open Data platform,
https://ais-faa.opendata.arcgis.com/datasets/class-airspace, the same
"xxx-faa.opendata.arcgis.com" family and the same ArcGIS org
(ssFJjBXIUyZDrSYZ) as FAA_UAS_FacilityMap_Data above) -- a polygon per
airspace surface area, covering the whole US/Puerto Rico/Virgin Islands.
Standard ArcGIS REST `query` semantics (bbox via esriGeometryEnvelope,
`f=geojson`) -- no API key needed, identical query mechanics to
faa_uas_facility_map.py's.

Field names (CLASS, LOCAL_TYPE, NAME, IDENT, LOWER_VAL/LOWER_UOM/
LOWER_CODE, UPPER_VAL/UPPER_UOM/UPPER_CODE) are confirmed from the FAA's
own published AIS Open Data Dictionary (aeronav.faa.gov), not guessed --
LOWER_UOM/UPPER_UOM are "FT" (feet) or "FL" (flight level, hundreds of
feet); LOWER_CODE/UPPER_CODE carry "SFC" (surface, i.e. 0 ft), "MSL",
"STD", or "UNLTD" (no ceiling).

Each surface area is imported as a `monitoring` zone (not `restricted`),
the same reasoning as the facility map's own: flying inside Class B/C/D/E
controlled airspace isn't automatically a real intrusion the way entering
a no-fly zone is, it means real-world ATC authorization/clearance would
be needed for that class -- tune your own deployment's incident-severity
mapping (app/incidents.py) if you want these treated more strictly than
the default monitoring severity.

This module was not validated against a live response from the endpoint
in the environment this was built in -- both aeronav.faa.gov (the Data
Dictionary PDF confirming these field names) and every arcgis.com-family
domain (including the FeatureServer itself) were unreachable from this
environment's network, so unlike the field names above (confirmed from
the FAA's own published documentation), DEFAULT_FEATURE_SERVER_URL
specifically is inferred from the same org/naming convention
FAA_UAS_FacilityMap_Data already uses, not confirmed by an actual request
against it. A search engine's own index does independently corroborate
this specific URL -- a crawled page titled "Class_Airspace FeatureServer
... loaded on July 21, 2025 ... operational ... Server Version 11.5" at
this exact org -- but that's still a crawler's snapshot, not this module
making its own live request. Sanity-check your first real import (or
pass your own --feature-server-url if it's moved) against a known Class
B/C/D airport's published airspace before relying on this.
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
_FEET_PER_FLIGHT_LEVEL_UNIT = 100.0

DEFAULT_FEATURE_SERVER_URL = (
    "https://services6.arcgis.com/ssFJjBXIUyZDrSYZ/arcgis/rest/services/"
    "Class_Airspace/FeatureServer/0/query"
)


def _altitude_to_m(value: float | None, uom: str | None, code: str | None) -> float | None:
    """None means "no cap" (UNLTD, or a missing/unusable value) -- never a
    fabricated number. SFC (surface) is a real, meaningful zero, not
    "missing", the same distinction app.airspace.faa_uas_facility_map's
    CEILING=0 handling already draws.
    """
    if code == "UNLTD":
        return None
    if code == "SFC":
        return 0.0
    if value is None:
        return None
    feet = value * _FEET_PER_FLIGHT_LEVEL_UNIT if uom == "FL" else value
    return feet * _FEET_TO_M


def _query_url(min_lon: float, min_lat: float, max_lon: float, max_lat: float, feature_server_url: str) -> str:
    params = {
        "where": "1=1",
        "outFields": "CLASS,LOCAL_TYPE,NAME,IDENT,LOWER_VAL,LOWER_UOM,LOWER_CODE,UPPER_VAL,UPPER_UOM,UPPER_CODE",
        "geometry": f"{min_lon},{min_lat},{max_lon},{max_lat}",
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "outSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "f": "geojson",
    }
    return f"{feature_server_url}?{urllib.parse.urlencode(params)}"


def fetch_class_airspace_geojson(
    min_lon: float, min_lat: float, max_lon: float, max_lat: float,
    feature_server_url: str = DEFAULT_FEATURE_SERVER_URL, timeout: float = 15.0,
) -> dict:
    """Query the FeatureServer for every Class Airspace surface area
    intersecting the given (lon/lat) bounding box, as a GeoJSON
    FeatureCollection.
    """
    url = _query_url(min_lon, min_lat, max_lon, max_lat, feature_server_url)
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read())


def geojson_to_zones(geojson: dict, name_prefix: str = "FAA Class Airspace") -> list[Zone]:
    """Convert a GeoJSON FeatureCollection of Class Airspace surface areas
    into this app's Zone model. GeoJSON polygon coordinates are (lon,
    lat), the opposite order of Zone.polygon's (lat, lon) -- swapped here,
    not left for a caller to get backwards.
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

        props = feature.get("properties") or {}
        airspace_class = props.get("CLASS") or props.get("LOCAL_TYPE") or "?"
        label = props.get("NAME") or props.get("IDENT") or f"#{i}"
        min_altitude_m = _altitude_to_m(props.get("LOWER_VAL"), props.get("LOWER_UOM"), props.get("LOWER_CODE"))
        max_altitude_m = _altitude_to_m(props.get("UPPER_VAL"), props.get("UPPER_UOM"), props.get("UPPER_CODE"))

        zones.append(
            Zone(
                name=f"{name_prefix}: {label} (Class {airspace_class})",
                zone_type=ZoneType.MONITORING,
                polygon=polygon,
                min_altitude_m=min_altitude_m,
                max_altitude_m=max_altitude_m,
                active=True,
            )
        )
    return zones


def import_class_airspace_zones(
    min_lon: float, min_lat: float, max_lon: float, max_lat: float, site_id: int,
    feature_server_url: str = DEFAULT_FEATURE_SERVER_URL,
) -> list[Zone]:
    """Fetch and persist every Class Airspace surface area in the given
    bounding box into `site_id`, skipping any zone name already in the
    database for that site (matches app.zones.load_zones_from_file's
    safe-to-rerun behavior).
    """
    geojson = fetch_class_airspace_geojson(min_lon, min_lat, max_lon, max_lat, feature_server_url)
    imported: list[Zone] = []
    for zone in geojson_to_zones(geojson):
        zone = zone.model_copy(update={"site_id": site_id})
        if get_zone_by_name(zone.name, site_id) is None:
            zone = create_zone(zone)
            logger.info(
                "Imported FAA Class Airspace zone '%s' (%s - %s)",
                zone.name,
                f"{zone.min_altitude_m:.0f}m" if zone.min_altitude_m is not None else "SFC",
                f"{zone.max_altitude_m:.0f}m" if zone.max_altitude_m is not None else "unlimited",
            )
        imported.append(zone)
    return imported
