"""Bridges a live dump1090 SBS-1 feed (TCP port 30003 by default) into
this tracker's POST /api/detections endpoint. Point dump1090 (or any
receiver emitting the SBS-1/BaseStation format) at an RTL-SDR dongle
tuned to 1090 MHz, then run this against its output.

Usage:
    python -m app.adapters.dump1090_bridge
    python -m app.adapters.dump1090_bridge --sbs-host 127.0.0.1 --sbs-port 30003 \\
        --api-url http://127.0.0.1:8000/api/detections --sensor-id dump1090-1
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import urllib.error
import urllib.request

from app.adapters.sbs1 import parse_sbs1_line


def post_detection(url: str, payload: dict, api_key: str = "") -> None:
    data = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=5):
        pass


def stream_lines(sock: socket.socket):
    """Yield complete lines from a socket that may deliver partial ones."""
    buffer = ""
    while True:
        chunk = sock.recv(4096).decode(errors="replace")
        if not chunk:
            return
        buffer += chunk
        while "\n" in buffer:
            line, buffer = buffer.split("\n", 1)
            yield line


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sbs-host", default="127.0.0.1", help="dump1090 host")
    parser.add_argument("--sbs-port", type=int, default=30003, help="dump1090 SBS-1 port")
    parser.add_argument("--api-url", default="http://127.0.0.1:8000/api/detections")
    parser.add_argument("--sensor-id", default="dump1090-1")
    parser.add_argument(
        "--api-key", default=os.getenv("DRONE_API_KEY", ""),
        help="X-API-Key header value; defaults to $DRONE_API_KEY",
    )
    args = parser.parse_args()

    print(f"Connecting to {args.sbs_host}:{args.sbs_port} ...")
    with socket.create_connection((args.sbs_host, args.sbs_port)) as sock:
        print(f"Connected. Posting detections to {args.api_url} as sensor '{args.sensor_id}'.")
        for line in stream_lines(sock):
            payload = parse_sbs1_line(line, sensor_id=args.sensor_id)
            if payload is None:
                continue
            try:
                post_detection(args.api_url, payload, args.api_key)
                print(f"-> {payload['latitude']:.5f}, {payload['longitude']:.5f}")
            except urllib.error.URLError as exc:
                print(f"ERROR posting detection: {exc}")


if __name__ == "__main__":
    main()
