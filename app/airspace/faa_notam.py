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
response before relying on it in production. This deliberately does NOT
attempt to turn a NOTAM into a Zone automatically: NOTAM geometry (when
present at all) isn't reliably a clean polygon the way the facility map's
is, and guessing that conversion risks a wrong restricted-zone shape,
which is worse than no zone at all -- these are returned as a plain list
for a human to review or a deployment to map to zones deliberately.
"""

from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://external-api.faa.gov/notamapi/v1/notams"


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
