"""Decodes real ASTM F3411 Remote ID broadcasts -- the standard every
drone over 250g sold in the US/EU is now required to transmit over
Bluetooth or Wi-Fi -- instead of app/remote_id.py's own signed-claim
scheme, which verifies a cryptographic assertion this app defines, not
anything a real drone actually broadcasts over the air.

This module is the transport-independent half: given already-extracted
fields from a decoded ASTM message (see app/adapters/astm_remote_id_ble_bridge.py
for the Bluetooth Low Energy transport, which does the actual decoding via
`dtpyodid` -- https://github.com/dronetag/python-odid, a real Python
implementation of the ASTM F3411 / ASD-STAN prEN 4709-002 message formats
from Dronetag, a commercial Remote ID hardware vendor, not a byte-offset
parser guessed from memory), it accumulates a device's state across
several broadcasts and builds a detection payload. Kept dependency-free
of `dtpyodid` itself, same as every other adapter's pure logic this
session (app/adapters/asterix.py, app/adapters/mavlink.py, ...) -- it's an
optional extra (requirements-remoteid.txt), not a core dependency, so the
testable logic here doesn't need it installed.

IMPORTANT -- this is NOT authenticated. Unlike app/remote_id.py's Ed25519
signature scheme, ASTM F3411 itself has no cryptographic authentication of
the OperatorID/BasicID fields -- this is a well-known, published
limitation of the standard as deployed today, not something a receiver
can add. A real broadcast claiming a given operator_id is exactly as
spoofable as the unsigned operator_id scheme this project's security
review already flagged and closed for its own signed-claim path --
receiving a real Remote ID broadcast must NOT, on its own, resolve to
`friendly`. If a deployment wants FRIENDLY status, cross-reference the
broadcast's OperatorID against a separately verified identity (e.g. this
project's own signed Remote ID path), don't trust the broadcast alone.

Over Bluetooth 4 Legacy Advertising, a transmitter sends one 25-byte ASTM
message per advertisement (BasicID, Location, System, OperatorID, SelfID,
or Auth), cycling through them -- not one bundled packet with everything.
A full picture (drone position + operator position + operator ID) needs
correlating several separate advertisements from the same transmitting
device over a short window; merge_fields does that accumulation, keyed by
whatever device identifier the transport layer provides (a BLE MAC
address for Bluetooth).
"""

from __future__ import annotations

from datetime import UTC, datetime


def merge_fields(state: dict, fields: dict) -> dict:
    """Folds one decoded message's extracted fields into a device's
    accumulated state, keeping whatever was already known for any key the
    new message doesn't carry (a Location update shouldn't erase an
    operator_id learned from an earlier OperatorID message).
    """
    return {**state, **{key: value for key, value in fields.items() if value is not None}}


def build_detection_payload(state: dict, sensor_id: str, confidence: float = 0.9) -> dict | None:
    """`state` is a device's accumulated fields from merge_fields(). A
    detection is only emitted once a Location message has actually been
    seen (there's nowhere else to get the drone's own position) --
    BasicID/OperatorID/SelfID/System alone aren't enough on their own.
    """
    latitude = state.get("latitude")
    longitude = state.get("longitude")
    if latitude is None or longitude is None:
        return None

    raw_data = {
        "protocol": "astm_f3411_remote_id",
        "uas_id": state.get("uas_id"),
        "ua_type": state.get("ua_type"),
        "operator_id": state.get("operator_id"),
        "operator_latitude": state.get("operator_latitude"),
        "operator_longitude": state.get("operator_longitude"),
        "description": state.get("description"),
        "speed_horizontal_mps": state.get("speed_horizontal_mps"),
        "direction_deg": state.get("direction_deg"),
        "status": state.get("status"),
        "altitude_geo_m": state.get("altitude_geo_m"),
        "altitude_baro_m": state.get("altitude_baro_m"),
    }
    return {
        "sensor_id": sensor_id,
        "sensor_type": "rf",
        "timestamp": datetime.now(UTC).replace(tzinfo=None).isoformat(),
        "latitude": latitude,
        "longitude": longitude,
        "altitude_m": state.get("height_m"),
        "confidence": confidence,
        "raw_data": raw_data,
    }
