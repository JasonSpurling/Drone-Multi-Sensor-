"""Bridges RUB-SysSec/DroneSecurity's DJI DroneID receiver output into this
tracker's POST /api/detections endpoint. See app/adapters/dji_droneid.py
for what this actually decodes (real OcuSync telemetry payload -- drone
GPS, operator GPS, serial number, home point) and the important caveats
about what could and couldn't be verified in the environment this was
built in.

This bridge itself has no SDR/hardware dependency -- it's a plain
JSON-lines stdin consumer, decoupled from the actual RF receiver entirely.
Run DroneSecurity's own receiver separately and pipe its output in:

Usage:
    # Against a live SDR (see DroneSecurity's own README for its setup --
    # this needs their tool, a supported SDR, and libuhd/UHD bindings,
    # none of which this repo provides):
    ./src/droneid_receiver_live.py | python -m app.adapters.dji_droneid_bridge --sensor-id dji-rf-1

    # Against a recorded capture, for testing the pipeline end-to-end
    # without live RF:
    ./src/droneid_receiver_offline.py -i samples/mini2_sm \\
        | python -m app.adapters.dji_droneid_bridge --sensor-id dji-rf-1
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error

from app.adapters.dji_droneid import build_detection_payload
from app.adapters.sdk import add_common_post_args, format_post_error, post_detection


def watch(args: argparse.Namespace) -> None:
    print(f"Reading DroneID JSON lines from stdin, posting to {args.api_url} as '{args.sensor_id}' ...")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            packet = json.loads(line)
        except json.JSONDecodeError as exc:
            print(f"Skipping unparseable line: {exc}")
            continue

        payload = build_detection_payload(packet, args.sensor_id, args.confidence)
        if payload is None:
            continue
        try:
            result = post_detection(
                args.api_url, payload, args.api_key,
                max_retries=args.max_retries, retry_backoff_s=args.retry_backoff,
            )
            serial = payload["raw_data"]["serial_number"]
            print(f"-> serial={serial} track {result.get('track_id')}")
        except urllib.error.URLError as exc:
            print(f"ERROR posting detection: {format_post_error(exc)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_common_post_args(parser, default_sensor_id="dji-droneid-1")
    parser.add_argument("--confidence", type=float, default=0.97)
    args = parser.parse_args()
    watch(args)


if __name__ == "__main__":
    main()
