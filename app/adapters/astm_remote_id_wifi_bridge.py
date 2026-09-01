"""Bridges live ASTM F3411 Remote ID broadcasts, received over WiFi
Beacon frames, into this tracker's POST /api/detections endpoint -- the
second of the two transports the standard defines (see
app/adapters/astm_remote_id_ble_bridge.py for Bluetooth Low Energy).
See app/adapters/astm_remote_id.py for what this decodes, its important
not-authenticated caveat, and why a full picture needs correlating
several broadcasts from the same device.

A drone broadcasting over WiFi rather than BLE sends its Open Drone ID
message pack inside a vendor-specific information element (802.11
element ID 0xDD) of its own Beacon frames -- the same frames a phone's
WiFi scan would see as a (usually fake/random-looking) access point.
Receiving them needs a WiFi adapter capable of *monitor mode* (most
built-in laptop WiFi chips can't do this; a cheap USB adapter with a
monitor-mode-capable chipset, e.g. one using the rtl8812au/mt7612u/
ath9k_htc drivers, is the usual DIY path) already switched into monitor
mode on the target channel before running this:

    sudo iw dev wlan0 interface add wlan0mon type monitor
    sudo ip link set wlan0mon up
    sudo iw dev wlan0mon set channel 6   # match the drone's actual channel

Usage:
    pip install -r requirements-remoteid.txt
    sudo .venv/bin/python -m app.adapters.astm_remote_id_wifi_bridge \\
        --interface wlan0mon --sensor-id remote-id-wifi-1

The vendor-specific element's byte layout this decodes
(app.adapters.astm_remote_id.parse_wifi_vendor_ie) was verified against
opendroneid/opendroneid-core-c's actual source (the reference C
implementation), not guessed from a byte-offset diagram -- see that
function's docstring for the specific structs it was checked against.
"""

from __future__ import annotations

import argparse
import time
import urllib.error

from app.adapters.astm_remote_id import (
    build_detection_payload,
    extract_message_fields,
    merge_fields,
    parse_wifi_vendor_ie,
)
from app.adapters.sdk import add_common_post_args, format_post_error, post_detection

# IEEE 802.11's standard "Vendor Specific" information element ID -- not
# drone-specific, the same tag WPA/WPS/every vendor's own beacon
# extensions use; app.adapters.astm_remote_id.parse_wifi_vendor_ie is what
# actually filters down to Open Drone ID's specific OUI within these.
_VENDOR_SPECIFIC_ELEMENT_ID = 221


def watch(args: argparse.Namespace) -> None:
    from scapy.layers.dot11 import Dot11, Dot11Beacon, Dot11Elt
    from scapy.sendrecv import sniff

    # Per-device accumulated state, keyed by the beacon's transmitter MAC
    # -- same accumulation strategy as the BLE bridge (see
    # app/adapters/astm_remote_id.py's docstring), same caveat that a
    # privacy-rotated MAC looks like a new device.
    device_state: dict[str, dict] = {}
    last_post: dict[str, float] = {}

    def _find_odid_message_pack(packet) -> bytes | None:
        element = packet.getlayer(Dot11Elt)
        while element is not None:
            if element.ID == _VENDOR_SPECIFIC_ELEMENT_ID:
                message_pack_bytes = parse_wifi_vendor_ie(bytes(element.info))
                if message_pack_bytes is not None:
                    return message_pack_bytes
            element = element.payload.getlayer(Dot11Elt)
        return None

    def _handle_beacon(packet) -> None:
        from dtpyodid.messages.messagepack import MessagePack
        from dtpyodid.parser import parse as parse_odid_message

        if not packet.haslayer(Dot11Beacon):
            return
        mac = packet.getlayer(Dot11).addr2
        if not mac:
            return

        message_pack_bytes = _find_odid_message_pack(packet)
        if message_pack_bytes is None:
            return
        decoded = parse_odid_message(message_pack_bytes)
        if decoded is None:
            return
        messages = decoded.messages if isinstance(decoded, MessagePack) else [decoded]

        state = device_state.get(mac, {})
        for message in messages:
            fields = extract_message_fields(message)
            if fields:
                state = merge_fields(state, fields)
        device_state[mac] = state

        now = time.monotonic()
        if now - last_post.get(mac, 0.0) < args.min_interval:
            return
        payload = build_detection_payload(state, sensor_id=args.sensor_id, confidence=args.confidence)
        if payload is None:
            return
        try:
            result = post_detection(
                args.api_url, payload, args.api_key,
                max_retries=args.max_retries, retry_backoff_s=args.retry_backoff,
            )
            print(f"-> {mac} uas_id={state.get('uas_id')} track {result.get('track_id')}")
            last_post[mac] = now
        except urllib.error.URLError as exc:
            print(f"ERROR posting detection: {format_post_error(exc)}")

    print(
        f"Sniffing {args.interface} for ASTM F3411 Remote ID WiFi Beacon frames, "
        f"posting to {args.api_url} as '{args.sensor_id}' ..."
    )
    sniff(iface=args.interface, prn=_handle_beacon, store=False)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--interface", required=True,
        help="Monitor-mode WiFi interface already up and on the right channel (e.g. wlan0mon) -- "
        "this bridge doesn't configure monitor mode or channel itself, see the module docstring",
    )
    add_common_post_args(parser, default_sensor_id="remote-id-wifi-1")
    parser.add_argument("--confidence", type=float, default=0.9)
    parser.add_argument("--min-interval", type=float, default=1.0, help="Seconds between posts per device")
    args = parser.parse_args()
    watch(args)


if __name__ == "__main__":
    main()
