"""Bridges live ASTM F3411 Remote ID broadcasts, received over Bluetooth
Low Energy, into this tracker's POST /api/detections endpoint. See
app/adapters/astm_remote_id.py for what this decodes, its important
not-authenticated caveat, and why a full picture needs correlating several
broadcasts from the same device.

Any standard Bluetooth adapter can receive these (no SDR needed, unlike
the DJI DroneID bridge) -- Remote ID's whole design point is that anyone
can passively receive it.

Usage:
    pip install -r requirements-remoteid.txt
    sudo .venv/bin/python -m app.adapters.astm_remote_id_ble_bridge --sensor-id remote-id-1

(Bluetooth scanning typically needs elevated privileges on Linux --
CAP_NET_ADMIN via setcap, or root, depending on your distro's BlueZ
policy; see bleak's own documentation if plain `sudo` doesn't work for
your setup.)
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request

from app.adapters.astm_remote_id import build_detection_payload, merge_fields

# The 16-bit UUID 0xFFFA ("ASTM International, ASTM Remote ID") expanded
# to the full 128-bit form bleak reports service_data keys as.
REMOTE_ID_SERVICE_UUID = "0000fffa-0000-1000-8000-00805f9b34fb"

# Per the OpenDroneID Bluetooth Legacy Advertising Service Data layout:
# byte 0 of the service data is this AD Application Code identifying Open
# Drone ID within ASTM's assigned address space, byte 1 is an 8-bit
# message counter, and the actual ODID message bytes start at byte 2.
# Verified against opendroneid/transmitter-linux's reference C
# implementation.
_AD_APPLICATION_CODE = 0x0D


def _extract_fields(message) -> dict | None:
    """Pulls the fields this bridge cares about out of one decoded
    dtpyodid message object, in the plain-dict shape
    app.adapters.astm_remote_id.merge_fields expects. Returns None for a
    message type this bridge doesn't use (Auth) or an unrecognized type.
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


def post_detection(url: str, payload: dict, api_key: str = "") -> dict:
    data = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read())


def watch(args: argparse.Namespace) -> None:
    from bleak import BleakScanner
    from dtpyodid.messages.messagepack import MessagePack
    from dtpyodid.parser import parse as parse_odid_message

    # Per-device accumulated state, keyed by BLE address -- a full picture
    # needs correlating several separate BT4 Legacy advertisements from
    # the same transmitter (see app/adapters/astm_remote_id.py's
    # docstring). Addresses can rotate for privacy, so a device that
    # changes its BLE address mid-flight will look like a new one; this
    # is a real limitation of BLE-based correlation, not something this
    # bridge can resolve without a stronger cross-broadcast identifier.
    device_state: dict[str, dict] = {}
    last_post: dict[str, float] = {}

    def _handle_messages(address: str, messages: list) -> None:
        state = device_state.get(address, {})
        for message in messages:
            fields = _extract_fields(message)
            if fields:
                state = merge_fields(state, fields)
        device_state[address] = state

        now = time.monotonic()
        if now - last_post.get(address, 0.0) < args.min_interval:
            return
        payload = build_detection_payload(state, sensor_id=args.sensor_id, confidence=args.confidence)
        if payload is None:
            return
        try:
            result = post_detection(args.api_url, payload, args.api_key)
            print(f"-> {address} uas_id={state.get('uas_id')} track {result.get('track_id')}")
            last_post[address] = now
        except urllib.error.URLError as exc:
            print(f"ERROR posting detection: {exc}")

    def detection_callback(device, advertisement_data) -> None:
        service_data = advertisement_data.service_data.get(REMOTE_ID_SERVICE_UUID)
        if not service_data or len(service_data) < 2 or service_data[0] != _AD_APPLICATION_CODE:
            return
        decoded = parse_odid_message(service_data[2:])
        if decoded is None:
            return
        messages = decoded.messages if isinstance(decoded, MessagePack) else [decoded]
        _handle_messages(device.address, messages)

    print(f"Scanning for ASTM F3411 Remote ID broadcasts, posting to {args.api_url} as '{args.sensor_id}' ...")

    import asyncio

    async def _run() -> None:
        # detection_callback runs independently (invoked by BleakScanner
        # itself on each advertisement); all this needs to do is keep the
        # scanner's context open indefinitely -- an Event that's never set
        # blocks forever without the wake-every-second overhead a sleep
        # loop has, and is the standard idiom for "run until cancelled."
        async with BleakScanner(detection_callback, service_uuids=[REMOTE_ID_SERVICE_UUID]):
            await asyncio.Event().wait()

    asyncio.run(_run())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--api-url", default="http://127.0.0.1:8000/api/detections")
    parser.add_argument("--sensor-id", default="remote-id-ble-1")
    parser.add_argument("--confidence", type=float, default=0.9)
    parser.add_argument("--min-interval", type=float, default=1.0, help="Seconds between posts per device")
    parser.add_argument("--api-key", default=os.getenv("DRONE_API_KEY", ""))
    args = parser.parse_args()
    watch(args)


if __name__ == "__main__":
    main()
