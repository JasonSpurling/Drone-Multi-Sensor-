"""Decodes actual DJI OcuSync "DroneID" telemetry -- not envelope/band
matching like app/rf_signatures.py, but the real payload: drone GPS
position, the *operator's* GPS position, serial number, and home point.
DJI transmits this unencrypted over OcuSync as an undocumented but
publicly disclosed broadcast (this is exactly what a DJI AeroScope
appliance decodes commercially).

This module does NOT do RF demodulation itself -- decoding OcuSync's
physical layer needs an actual SDR (the reference implementation below was
built against an Ettus USRP B205-mini) and low-level UHD bindings, which
is real signal-processing hardware/software entirely outside what a pip
package or this app can provide, and outside what could be tested in the
environment this was built in (no SDR hardware, no captured IQ samples).

Instead, this is a bridge in the same role as app/adapters/dump1090_bridge.py
for dump1090: it consumes the JSON output of a separate, real external
tool that does the actual RF work --
`RUB-SysSec/DroneSecurity <https://github.com/RUB-SysSec/DroneSecurity>`_,
the open-source SDR receiver/decoder published alongside "Drone Security
and the Mysterious Case of DJI's DroneID" (NDSS 2023), the peer-reviewed
paper that reverse-engineered this protocol. Run their receiver (live
against an SDR, or offline against a capture) and pipe its JSON-lines
output into this bridge:

    ./src/droneid_receiver_live.py | python -m app.adapters.dji_droneid_bridge --sensor-id dji-rf-1

IMPORTANT -- what's verified here and what isn't: the JSON field names
below (serial_number, latitude/longitude, app_lat/app_lon, latitude_home/
longitude_home, crc-packet/crc-calculated) match that project's own
documented example output, fetched and confirmed from its README. What
this module could NOT be verified against, in the environment it was
built in: an actual live SDR, a real captured DroneID transmission, or
DJI's own specification (there isn't one -- OcuSync/DroneID is proprietary
and undocumented; RUB-SysSec's paper is an independent reverse-engineering
effort, not an official spec, so exact fields available may vary by drone
model/firmware). Sanity-check your first real decoded packet against a
known drone before relying on this.

A decoded packet includes the operator's real-time location -- genuinely
sensitive personal data, not just aircraft telemetry. That's inherent to
what DroneID actually broadcasts (a disclosed DJI design choice, not
something this module adds), and is exactly the capability a legitimate
counter-drone deployment needs (identifying where to send a security
response), the same use case DJI's own AeroScope is sold for. It's kept
out of the tracked `drone` detection's lat/lon (which is the aircraft's
position) and carried only in raw_data -- treat it with the same handling
care you'd give any personal location data in your deployment.
"""

from __future__ import annotations

from datetime import UTC, datetime


def is_valid_packet(packet: dict) -> bool:
    """DroneSecurity reports a CRC check per decoded packet; a mismatch
    means the demodulated bits are corrupted (a real risk of OTA RF
    decode, not a network/parsing issue) and the telemetry shouldn't be
    trusted. Packets with no CRC fields at all (a different tool version,
    or a hand-built test fixture) aren't rejected on that basis alone.
    """
    reported = packet.get("crc-packet")
    calculated = packet.get("crc-calculated")
    if reported is None or calculated is None:
        return True
    return reported == calculated


def build_detection_payload(packet: dict, sensor_id: str, confidence: float = 0.97) -> dict | None:
    """`packet` is one decoded JSON object from DroneSecurity's receiver
    output. Returns a POST /api/detections payload for the *drone's* own
    position, or None if the packet has no position or failed its CRC
    check. Confidence defaults high: unlike RF envelope matching, this is
    the aircraft's actual broadcast GPS position, not an inference from
    signal shape.
    """
    if not is_valid_packet(packet):
        return None

    latitude = packet.get("latitude")
    longitude = packet.get("longitude")
    if latitude is None or longitude is None:
        return None

    return {
        "sensor_id": sensor_id,
        "sensor_type": "rf",
        "timestamp": datetime.now(UTC).replace(tzinfo=None).isoformat(),
        "latitude": latitude,
        "longitude": longitude,
        "altitude_m": packet.get("altitude"),
        "confidence": confidence,
        "raw_data": {
            "protocol": "dji_droneid_ocusync",
            "serial_number": packet.get("serial_number"),
            "operator_latitude": packet.get("app_lat"),
            "operator_longitude": packet.get("app_lon"),
            "home_latitude": packet.get("latitude_home"),
            "home_longitude": packet.get("longitude_home"),
        },
    }
