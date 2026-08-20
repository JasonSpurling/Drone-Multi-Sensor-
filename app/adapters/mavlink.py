"""Builds a detection payload from a decoded MAVLink GLOBAL_POSITION_INT
message -- the position report a MAVLink-speaking drone (ArduPilot, PX4,
and most hobbyist/commercial flight controllers) sends over its telemetry
link. Interceptable on an unencrypted telemetry radio (the common case for
hobbyist gear: 433/915 MHz SiK radios, WFB-ng, etc.) the same way this
tracker's other adapters intercept RF/ADS-B/camera signal.

IMPORTANT -- this is NOT authenticated: unlike app/remote_id.py's signed
Remote ID path, a MAVLink telemetry intercept carries no cryptographic
proof of who's flying it. A detection built here reports a high confidence
(only drones speak MAVLink, so hearing it at all is strong positive
evidence something is a drone) but never resolves to `friendly` on its
own -- if you control the aircraft and want it classified friendly, also
register/sign it via app/remote_id.py; this adapter is a raw position
source, not an identity channel.
"""

from __future__ import annotations

from datetime import UTC, datetime

# MAVLink's sentinel for "heading unknown" in GLOBAL_POSITION_INT.hdg (centidegrees).
_HEADING_UNKNOWN_CDEG = 65535


def build_detection_payload(
    sensor_id: str,
    sysid: int,
    lat_e7: int,
    lon_e7: int,
    alt_mm: int,
    heading_cdeg: int,
    vx_cms: int,
    vy_cms: int,
    confidence: float = 0.95,
) -> dict | None:
    """`lat_e7`/`lon_e7`/`alt_mm`/`heading_cdeg`/`vx_cms`/`vy_cms` are the
    raw GLOBAL_POSITION_INT fields (degrees*1e7, degrees*1e7, millimeters,
    centidegrees, cm/s, cm/s). Returns None if the vehicle hasn't reported
    a GPS fix yet -- MAVLink's convention for "unknown position" in this
    message is exactly (0, 0), not a null/NaN.
    """
    if lat_e7 == 0 and lon_e7 == 0:
        return None

    heading_deg = None if heading_cdeg == _HEADING_UNKNOWN_CDEG else heading_cdeg / 100.0
    ground_speed_mps = ((vx_cms**2 + vy_cms**2) ** 0.5) / 100.0

    return {
        "sensor_id": sensor_id,
        "sensor_type": "other",
        "timestamp": datetime.now(UTC).replace(tzinfo=None).isoformat(),
        "latitude": lat_e7 / 1e7,
        "longitude": lon_e7 / 1e7,
        "altitude_m": alt_mm / 1000.0,
        "confidence": confidence,
        "raw_data": {
            "protocol": "mavlink",
            "mavlink_sysid": sysid,
            "heading_deg": heading_deg,
            "ground_speed_mps": ground_speed_mps,
        },
    }
