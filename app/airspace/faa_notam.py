"""Client for the FAA NOTAM API (api.faa.gov) -- Notices to Air Missions,
covering airspace restrictions/hazards a static zone file or the facility
map ceiling grid can't (temporary flight restrictions, a UAS operating
area closed for an event, a stadium TFR, ...).

IMPORTANT -- unlike this tracker's other real integrations this session
(ASTERIX via asterix4py, MAVLink via pymavlink, the FAA UAS Facility Map
importer), this client was NOT validated against a live response: it needs
a registered client_id/client_secret from the api.faa.gov developer portal
(free to register, but both registration and api.faa.gov itself were
unreachable from the network this was built in -- outbound access here is
restricted to a small allowlist), and multiple third-party integrations
report the FAA has made this specific surface progressively harder to use
as it migrates toward a newer NOTAM Management Service. The request shape
below (client_id/client_secret as headers, responseFormat=geoJson,
locationLongitude/locationLatitude/locationRadius as query params) matches
the documented api.faa.gov contract, and response parsing only reads the
well-established top-level NOTAM fields rather than assuming a rigid full
schema -- but verify this against your own registered account's actual
response before relying on it in production.

fetch_notams() itself never guesses at a NOTAM's real geometry: NOTAM
geometry (when present at all) isn't reliably a clean polygon the way the
facility map's is, and guessing that conversion risks a wrong restricted-
zone shape, which is worse than no zone at all -- it returns a plain list
for a human to review. notams_to_zones()/import_notams_as_zones() below
DO turn that list into real zones, but deliberately using only what's
actually known with certainty about each one: the search circle
(center point + radius) that was queried to find it, not a fabricated
guess at the NOTAM's own precise footprint. A zone built this way reads
honestly as "a NOTAM is active somewhere within this circle" -- exactly
what fetch_notams() actually proves -- not a false claim about that
NOTAM's real boundary. Prefer creating a zone by hand (POST /api/zones)
for any NOTAM whose real geometry you actually know (e.g. parsed from its
raw text) instead of relying on this approximation.
"""

from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request

from app.db import create_zone, get_zone_by_name
from app.geo import destination_point
from app.models import Zone, ZoneType

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://external-api.faa.gov/notamapi/v1/notams"
_NM_TO_M = 1852.0
_DEFAULT_ZONE_VERTICES = 16


def _simplify_notam(item: dict) -> dict | None:
    core = ((item.get("properties") or {}).get("coreNOTAMData") or {}).get("notam")
    if not core:
        return None
    return {
        "number": core.get("number"),
        "text": core.get("text") or core.get("traditionalMessage"),
        "classification": core.get("classification"),
        "effective_start": core.get("effectiveStart"),
        "effective_end": core.get("effectiveEnd"),
        "icao_location": core.get("icaoLocation"),
    }


def fetch_notams(
    client_id: str,
    client_secret: str,
    lat: float,
    lon: float,
    radius_nm: float = 100.0,
    base_url: str = DEFAULT_BASE_URL,
    timeout: float = 15.0,
) -> list[dict]:
    """Fetch active NOTAMs within radius_nm nautical miles of (lat, lon),
    as a list of simplified dicts (number/text/classification/effective
    window/ICAO location) rather than the raw API response.
    """
    params = {
        "locationLongitude": lon,
        "locationLatitude": lat,
        "locationRadius": radius_nm,
        "responseFormat": "geoJson",
    }
    url = f"{base_url}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(
        url, headers={"client_id": client_id, "client_secret": client_secret, "Accept": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = json.loads(response.read())

    simplified = (_simplify_notam(item) for item in raw.get("items", []))
    return [notam for notam in simplified if notam is not None]


def _search_circle_polygon(
    center_lat: float, center_lon: float, radius_nm: float, num_vertices: int
) -> list[tuple[float, float]]:
    radius_m = radius_nm * _NM_TO_M
    return [
        destination_point(center_lat, center_lon, 360.0 * i / num_vertices, radius_m)
        for i in range(num_vertices)
    ]


def notams_to_zones(
    notams: list[dict],
    center_lat: float,
    center_lon: float,
    radius_nm: float,
    num_vertices: int = _DEFAULT_ZONE_VERTICES,
) -> list[Zone]:
    """Turns each NOTAM dict (as returned by fetch_notams, called with
    this same center_lat/center_lon/radius_nm) into a Zone shaped as the
    search circle that was actually queried to find it -- see this
    module's docstring for why that's the honest choice instead of
    guessing at a NOTAM's own geometry. Every NOTAM from one fetch_notams
    call gets the identical circle (that's the area that was genuinely
    searched); what distinguishes them is the zone name/description, not
    the shape.
    """
    polygon = _search_circle_polygon(center_lat, center_lon, radius_nm, num_vertices)
    zones = []
    for notam in notams:
        number = notam.get("number") or "unknown"
        location = notam.get("icao_location")
        name = f"NOTAM {number}" + (f" ({location})" if location else "")
        zones.append(
            Zone(
                name=name,
                zone_type=ZoneType.RESTRICTED,
                polygon=polygon,
                active=True,
            )
        )
    return zones


def import_notams_as_zones(
    client_id: str,
    client_secret: str,
    lat: float,
    lon: float,
    site_id: int,
    radius_nm: float = 100.0,
    base_url: str = DEFAULT_BASE_URL,
) -> list[Zone]:
    """Fetch active NOTAMs near (lat, lon) and persist each as an
    approximate circular zone (see notams_to_zones), skipping any zone
    name already in the database for this site -- matches
    app.airspace.faa_uas_facility_map.import_facility_map_zones' and
    app.zones.load_zones_from_file's safe-to-rerun behavior, so this can
    be re-run periodically (e.g. from a cron job) without piling up
    duplicate zones for a NOTAM still active from a previous run.
    """
    notams = fetch_notams(client_id, client_secret, lat, lon, radius_nm, base_url)
    imported: list[Zone] = []
    for zone in notams_to_zones(notams, lat, lon, radius_nm):
        zone = zone.model_copy(update={"site_id": site_id})
        if get_zone_by_name(zone.name, site_id) is None:
            zone = create_zone(zone)
            logger.info(
                "Imported NOTAM zone '%s' (approximate search-circle boundary, not the NOTAM's "
                "real footprint -- see this module's docstring)",
                zone.name,
            )
        imported.append(zone)
    return imported
