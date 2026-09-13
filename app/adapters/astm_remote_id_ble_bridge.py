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
import time
import urllib.error

from app.adapters.astm_remote_id import (
    DIRECT_REMOTE_ID_APPLICATION_CODE,
    build_detection_payload,
    extract_message_fields,
    merge_fields,
)
from app.adapters.sdk import add_common_post_args, format_post_error, post_detection

# The 16-bit UUID 0xFFFA ("ASTM International, ASTM Remote ID") expanded
# to the full 128-bit form bleak reports service_data keys as.
REMOTE_ID_SERVICE_UUID = "0000fffa-0000-1000-8000-00805f9b34fb"


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
            fields = extract_message_fields(message)
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
            result = post_detection(
                args.api_url, payload, args.api_key,
                max_retries=args.max_retries, retry_backoff_s=args.retry_backoff,
            )
            print(f"-> {address} uas_id={state.get('uas_id')} track {result.get('track_id')}")
            last_post[address] = now
        except urllib.error.URLError as exc:
            print(f"ERROR posting detection: {format_post_error(exc)}")

    def detection_callback(device, advertisement_data) -> None:
        service_data = advertisement_data.service_data.get(REMOTE_ID_SERVICE_UUID)
        if not service_data or len(service_data) < 2 or service_data[0] != DIRECT_REMOTE_ID_APPLICATION_CODE:
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
    add_common_post_args(parser, default_sensor_id="remote-id-ble-1")
    parser.add_argument("--confidence", type=float, default=0.9)
    parser.add_argument("--min-interval", type=float, default=1.0, help="Seconds between posts per device")
    args = parser.parse_args()
    watch(args)


if __name__ == "__main__":  # pragma: no cover -- only executes when this
    # file is run directly as a script (python -m / a shebang), never when
    # imported under pytest, so it is structurally unreachable in-process;
    # main()'s own body is covered by tests that call it directly.
    main()
