"""Bridges a live MAVLink telemetry link into this tracker's
POST /api/detections endpoint, via GLOBAL_POSITION_INT messages (see
app/adapters/mavlink.py for what this does and doesn't prove about the
vehicle's identity).

Usage:
    pip install -r requirements-mavlink.txt
    # A SiK telemetry radio, SITL, or any MAVLink source pymavlink can open:
    python -m app.adapters.mavlink_bridge --source udp:127.0.0.1:14550 --sensor-id mavlink-1
    python -m app.adapters.mavlink_bridge --source /dev/ttyUSB0 --baud 57600
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request

from app.adapters.mavlink import build_detection_payload


def post_detection(url: str, payload: dict, api_key: str = "") -> dict:
    data = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read())


def watch(args: argparse.Namespace) -> None:
    from pymavlink import mavutil

    connection = mavutil.mavlink_connection(args.source, baud=args.baud)
    print(f"Waiting for MAVLink heartbeat on {args.source} ...")
    connection.wait_heartbeat()
    print(f"Connected (sysid={connection.target_system}). Posting to {args.api_url} as '{args.sensor_id}' ...")

    while True:
        msg = connection.recv_match(type="GLOBAL_POSITION_INT", blocking=True, timeout=args.recv_timeout)
        if msg is None:
            continue
        payload = build_detection_payload(
            sensor_id=args.sensor_id,
            sysid=msg.get_srcSystem(),
            lat_e7=msg.lat,
            lon_e7=msg.lon,
            alt_mm=msg.alt,
            heading_cdeg=msg.hdg,
            vx_cms=msg.vx,
            vy_cms=msg.vy,
            confidence=args.confidence,
        )
        if payload is None:
            continue
        try:
            result = post_detection(args.api_url, payload, args.api_key)
            print(f"-> sysid={msg.get_srcSystem()} track {result.get('track_id')}")
        except urllib.error.URLError as exc:
            print(f"ERROR posting detection: {exc}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--source", default="udp:127.0.0.1:14550",
        help="pymavlink connection string: udp:host:port, tcp:host:port, or a serial device path",
    )
    parser.add_argument("--baud", type=int, default=57600, help="Baud rate for a serial --source")
    parser.add_argument("--recv-timeout", type=float, default=5.0)
    parser.add_argument("--api-url", default="http://127.0.0.1:8000/api/detections")
    parser.add_argument("--sensor-id", default="mavlink-1")
    parser.add_argument("--confidence", type=float, default=0.95)
    parser.add_argument("--api-key", default=os.getenv("DRONE_API_KEY", ""))
    args = parser.parse_args()
    watch(args)


if __name__ == "__main__":
    main()
