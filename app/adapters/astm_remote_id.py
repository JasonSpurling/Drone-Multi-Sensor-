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
address for Bluetooth, or the transmitting MAC in a WiFi Beacon frame --
see app/adapters/astm_remote_id_wifi_bridge.py for that transport).

extract_message_fields() and parse_wifi_vendor_ie() below are also
transport-independent in the sense that matters here: which bytes to
pull an already-decoded dtpyodid message's fields from, and how to find
Open Drone ID's message-pack bytes inside a raw WiFi vendor-specific
information element, don't depend on *how* those bytes reached this
process (BLE GATT/advertising vs. an 802.11 monitor-mode capture).
"""

from __future__ import annotations

from datetime import UTC, datetime

# Per the OpenDroneID Bluetooth Legacy Advertising Service Data layout
# AND the WiFi Beacon vendor-specific element layout (verified against
# opendroneid/opendroneid-core-c's actual source: libopendroneid/wifi.c's
# odid_wifi_build_message_pack_beacon_frame sets vendor->oui_type = 0x0D;
# libopendroneid/odid_wifi.h's struct ODID_service_info is {uint8_t
# message_counter; ODID_MessagePack_encoded odid_message_pack[];}, packed)
# -- this AD Application Code identifies Open Drone ID / Direct Remote ID
# within ASTM's assigned address space, on both transports. What differs
# per transport is only what comes before it: BLE's service data starts
# with this byte directly (see astm_remote_id_ble_bridge.py); WiFi's
# vendor element prefixes it with a 3-byte OUI (see
# ASD_STAN_WIFI_VENDOR_OUI and parse_wifi_vendor_ie below).
DIRECT_REMOTE_ID_APPLICATION_CODE = 0x0D

# The OUI ASD-STAN (the European standards body co-defining Direct Remote
# ID with ASTM) was assigned for this, per opendroneid-core-c's wifi.c
# (`asd_stan_oui = {0xFA, 0x0B, 0xBC}` in odid_wifi_build_message_pack_beacon_frame).
ASD_STAN_WIFI_VENDOR_OUI = b"\xfa\x0b\xbc"


def parse_wifi_vendor_ie(info: bytes) -> bytes | None:
    """Given one WiFi vendor-specific information element's payload bytes
    (element ID 0xDD's contents -- the octets after the leading
    element-id+length bytes any 802.11 frame parser, e.g. scapy's
    Dot11Elt.info, already strips), returns the Open Drone ID
    message-pack bytes if this IE matches Open Drone ID's real
    over-the-air layout, or None if it's some other vendor's IE (WiFi
    beacons routinely carry several -- WPS, vendor QoS extensions, ...)
    or too short to be one at all.

    Layout: 3-byte OUI, 1-byte application code, 1-byte message counter,
    then the message-pack bytes themselves -- see
    DIRECT_REMOTE_ID_APPLICATION_CODE's docstring for the source this was
    verified against; not a byte offset guessed from memory.
    """
    if len(info) < 5:
        return None
    if info[0:3] != ASD_STAN_WIFI_VENDOR_OUI or info[3] != DIRECT_REMOTE_ID_APPLICATION_CODE:
        return None
    return info[5:]  # info[4] is the message_counter -- not needed by this bridge


def extract_message_fields(message) -> dict | None:
    """Pulls the fields this project cares about out of one decoded
    dtpyodid message object, in the plain-dict shape merge_fields()
    expects. Returns None for a message type not used here (Auth) or an
    unrecognized type. Shared by every transport bridge (BLE, WiFi) --
    dtpyodid's own message classes are transport-agnostic; only how the
    bytes reached the decoder differs.
    """
    from dtpyodid.messages.basicid import BasicID
    from dtpyodid.messages.location import Location
    from dtpyodid.messages.operatorid import OperatorID
    from dtpyodid.messages.selfid import SelfID
    from dtpyodid.messages.system import System

    if isinstance(message, BasicID):
        # A real upstream quirk: BasicID._parse's id_type/ua_type end up
        # as 1-tuples, not plain ints, because of a trailing comma in
        # dtpyodid's own source (`id_type = (...) >> 4,`). Unwrapped here
        # rather than assumed fixed upstream.
        ua_type = message.ua_type[0] if isinstance(message.ua_type, tuple) else message.ua_type
        return {"uas_id": message.uas_id.rstrip("\0").strip(), "ua_type": ua_type}
    if isinstance(message, Location):
        return {
            "latitude": message.latitude,
            "longitude": message.longitude,
            "height_m": message.height,
            "altitude_geo_m": message.altitude_geo,
            "altitude_baro_m": message.altitude_baro,
            "speed_horizontal_mps": message.speed_horizontal,
            "direction_deg": message.direction,
            "status": message.status.name,
        }
    if isinstance(message, System):
        return {"operator_latitude": message.latitude, "operator_longitude": message.longitude}
    if isinstance(message, OperatorID):
        return {"operator_id": message.operator_id.rstrip("\0").strip()}
    if isinstance(message, SelfID):
        return {"description": message.desc.rstrip("\0").strip()}
    return None


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
