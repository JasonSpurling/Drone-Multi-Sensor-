"""Parser for the SBS-1/BaseStation text protocol emitted by dump1090 and
similar ADS-B decoders (typically served on TCP port 30003) -- the most
common real-world way to get ADS-B detections into this tracker, off
about $20 of RTL-SDR hardware. See app/adapters/dump1090_bridge.py for a
bridge script that connects to a live feed and posts parsed detections to
the API.

One SBS-1 line looks like (fields 1-22, comma-separated):
    MSG,3,1,1,4CA593,1,2026-08-18,12:34:56.789,2026-08-18,12:34:56.789,,
    38000,,,51.4700,-0.4543,,,,,,0

Only "MSG,3" (airborne position) messages carry lat/lon/altitude, which is
all this tracker needs -- other message types (identification, velocity,
etc.) are ignored.
"""

from __future__ import annotations

from datetime import datetime, timezone

_MIN_FIELD_COUNT = 16
_MSG_TYPE_INDEX = 1
_HEX_IDENT_INDEX = 4
_ALTITUDE_INDEX = 11
_LATITUDE_INDEX = 14
_LONGITUDE_INDEX = 15

_AIRBORNE_POSITION_MSG_TYPE = "3"
_FEET_TO_METERS = 0.3048


def parse_sbs1_line(line: str, sensor_id: str = "dump1090-1") -> dict | None:
    """Parse one SBS-1 CSV line into a detection payload dict ready to
    POST to /api/detections, or None if the line isn't a usable airborne
    position message.
    """
    fields = line.strip().split(",")
    if len(fields) < _MIN_FIELD_COUNT or fields[0] != "MSG":
        return None
    if fields[_MSG_TYPE_INDEX] != _AIRBORNE_POSITION_MSG_TYPE:
        return None

    hex_ident = fields[_HEX_IDENT_INDEX].strip()
    latitude = fields[_LATITUDE_INDEX].strip()
    longitude = fields[_LONGITUDE_INDEX].strip()
    if not hex_ident or not latitude or not longitude:
        return None

    payload: dict = {
        "sensor_id": sensor_id,
        "sensor_type": "adsb",
        "timestamp": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
        "latitude": float(latitude),
        "longitude": float(longitude),
        "confidence": 0.99,
        "raw_data": {"hex_ident": hex_ident},
    }

    altitude = fields[_ALTITUDE_INDEX].strip()
    if altitude:
        payload["altitude_m"] = round(float(altitude) * _FEET_TO_METERS, 1)

    return payload
