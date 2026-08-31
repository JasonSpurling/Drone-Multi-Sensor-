"""Imports real FAA Special Use Airspace data -- Prohibited, Restricted,
Warning, Alert, Military Operations, and National Security Areas, the
same six categories drawn on every VFR sectional chart -- as zones. This
is the most directly relevant FAA data source in app/airspace/ for a
drone-detection deployment specifically: unlike Class Airspace's B/C/D/E
(app/airspace/faa_class_airspace.py, where crossing the boundary just
means ATC coordination is needed), Prohibited and Restricted areas are
areas flight is genuinely not permitted in without specific clearance --
P-56 over the White House/Capitol, R-4401 over a military range, and
similar are exactly the kind of place an unauthorized drone sighting is
most consequential.

Source: FAA's public, unauthenticated ArcGIS FeatureServer
(Special_Use_Airspace, published via FAA's AIS Open Data platform,
https://adds-faa.opendata.arcgis.com/datasets/special-use-airspace --
same "xxx-faa.opendata.arcgis.com" family and the same ArcGIS org
(ssFJjBXIUyZDrSYZ) as FAA_UAS_FacilityMap_Data and Class_Airspace
above). Standard ArcGIS REST `query` semantics (bbox via
esriGeometryEnvelope, `f=geojson`) -- no API key needed, identical query
mechanics to the other two airspace modules.

Fields (TYPE, NAME, LOWER_ALT, UPPER_ALT) are confirmed present on this
layer from its own public metadata, but -- unlike Class Airspace, where
the FAA's Data Dictionary PDF gave an exact, confirmed set of TYPE/CODE
enum values -- that document's Special Use Airspace section wasn't
reachable from the environment this was built in, so the exact string
values TYPE and the LOWER_ALT/UPPER_ALT altitude fields use weren't
directly confirmed the same way. What IS reliable, independent of any
single field's exact encoding, is the SUA name/designator convention
used on every real FAA chart and in every public reference on the
subject (also independently confirmed): a name literally starting with
"P-" is Prohibited, "R-" is Restricted, "W-" is Warning, "A-" is Alert
-- classify_sua_type() below keys off that instead of trusting one
uncertain field's exact casing/spelling. LOWER_ALT/UPPER_ALT are parsed
defensively for the same reason: "SFC" (surface) and "UNL"/"UNLTD"/
"UNLIMITED" are the universal conventions across every FAA aeronautical
layer already integrated here, handled the same way regardless of which
exact spelling this particular layer uses; anything else is parsed as a
plain number of feet.

Prohibited and Restricted areas import as `no_fly` zones (the closest
match this app's ZoneType has to "flight genuinely not permitted without
specific clearance") -- every other category (Warning, Alert, MOA,
National Security Area) imports as `monitoring`, the same "be aware, not
an automatic intrusion" reasoning app/airspace/faa_class_airspace.py and
app/airspace/faa_uas_facility_map.py already use for their own zones.

This module was not validated against a live response from the endpoint
in the environment this was built in -- every FAA/ArcGIS domain was
unreachable from this environment's network, same caveat as this
package's other two modules. Sanity-check your first real import against
a known Prohibited or Restricted area (P-56 is a common, well-documented
reference point) before relying on this for anything safety-relevant.
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
    "Special_Use_Airspace/FeatureServer/0/query"
)

# Areas flight is genuinely not permitted in without specific clearance --
# the closest match this app's ZoneType has is NO_FLY, not RESTRICTED
# (which this app's own zone-type vocabulary reserves for a
# user-configured restriction, see app/models.py's ZoneType).
_NO_FLY_PREFIXES = ("P-", "R-")


def classify_sua_type(name: str | None, type_field: str | None) -> ZoneType:
    """Prefers the well-known designator-prefix convention on `name`
    ("P-56", "R-4401", ...) over trusting `type_field`'s exact spelling --
    see the module docstring for why. Falls back to `type_field` (a loose
    substring match, not an exact enum comparison, for the same reason)
    only when `name` doesn't start with a recognized prefix; defaults to
    MONITORING (the less consequential of the two, not NO_FLY) when
    neither field gives a confident answer, rather than guessing toward
    the stricter classification from ambiguous data.
    """
    if name:
        stripped = name.strip().upper()
        if stripped.startswith(_NO_FLY_PREFIXES):
            return ZoneType.NO_FLY
    if type_field:
        upper = type_field.strip().upper()
        if "PROHIBIT" in upper or "RESTRICT" in upper:
            return ZoneType.NO_FLY
    return ZoneType.MONITORING


def _altitude_to_m(raw: object) -> float | None:
    """None means "no cap" (unlimited, or a missing/unusable value) --
    never a fabricated number. Accepts a string ("SFC", "UNL", a plain
    number) or an already-numeric value, since which of those this
    particular layer actually returns per field wasn't confirmed (see
    module docstring).
    """
    if raw is None:
        return None
    if isinstance(raw, int | float):
        return float(raw) * _FEET_TO_M
    text = str(raw).strip().upper()
    if not text:
        return None
    if text == "SFC":
        return 0.0
    if text in ("UNL", "UNLTD", "UNLIMITED"):
        return None
    try:
        return float(text) * _FEET_TO_M
    except ValueError:
        return None


def _query_url(min_lon: float, min_lat: float, max_lon: float, max_lat: float, feature_server_url: str) -> str:
    params = {
        "where": "1=1",
        "outFields": "TYPE,NAME,LOWER_ALT,UPPER_ALT",
        "geometry": f"{min_lon},{min_lat},{max_lon},{max_lat}",
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "outSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "f": "geojson",
    }
    return f"{feature_server_url}?{urllib.parse.urlencode(params)}"


def fetch_special_use_airspace_geojson(
    min_lon: float, min_lat: float, max_lon: float, max_lat: float,
    feature_server_url: str = DEFAULT_FEATURE_SERVER_URL, timeout: float = 15.0,
) -> dict:
    """Query the FeatureServer for every Special Use Airspace area
    intersecting the given (lon/lat) bounding box, as a GeoJSON
    FeatureCollection.
    """
    url = _query_url(min_lon, min_lat, max_lon, max_lat, feature_server_url)
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read())


def geojson_to_zones(geojson: dict, name_prefix: str = "FAA SUA") -> list[Zone]:
    """Convert a GeoJSON FeatureCollection of Special Use Airspace areas
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
        name = props.get("NAME")
        label = name or f"#{i}"
        zone_type = classify_sua_type(name, props.get("TYPE"))
        min_altitude_m = _altitude_to_m(props.get("LOWER_ALT"))
        max_altitude_m = _altitude_to_m(props.get("UPPER_ALT"))

        zones.append(
            Zone(
                name=f"{name_prefix}: {label}",
                zone_type=zone_type,
                polygon=polygon,
                min_altitude_m=min_altitude_m,
                max_altitude_m=max_altitude_m,
                active=True,
            )
        )
    return zones


def import_special_use_airspace_zones(
    min_lon: float, min_lat: float, max_lon: float, max_lat: float, site_id: int,
    feature_server_url: str = DEFAULT_FEATURE_SERVER_URL,
) -> list[Zone]:
    """Fetch and persist every Special Use Airspace area in the given
    bounding box into `site_id`, skipping any zone name already in the
    database for that site (matches app.zones.load_zones_from_file's
    safe-to-rerun behavior).
    """
    geojson = fetch_special_use_airspace_geojson(min_lon, min_lat, max_lon, max_lat, feature_server_url)
    imported: list[Zone] = []
    for zone in geojson_to_zones(geojson):
        zone = zone.model_copy(update={"site_id": site_id})
        if get_zone_by_name(zone.name, site_id) is None:
            zone = create_zone(zone)
            logger.info("Imported FAA Special Use Airspace zone '%s' (%s)", zone.name, zone.zone_type.value)
        imported.append(zone)
    return imported
