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
import socket
import time
import urllib.error
import urllib.request

from app.adapters.sbs1 import parse_sbs1_line
from app.adapters.sdk import add_common_post_args, format_post_error, post_detection


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


def parse_aircraft_categories(aircraft_json: dict) -> dict[str, str]:
    """`aircraft_json` is a decoded dump1090/dump1090-fa/readsb aircraft.json
    response (`{"aircraft": [{"hex": "4ca593", "category": "A3", ...}, ...],
    ...}`) -- the real ICAO ADS-B emitter category per aircraft (DO-260B
    Table 2-36: A1-A7 fixed-wing/rotorcraft weight/performance classes,
    B1-B7 glider/balloon/UAV/etc). The SBS-1 text feed this bridge
    otherwise reads (app/adapters/sbs1.py) doesn't carry this field at
    all -- it's only available from the richer JSON output, a genuinely
    separate endpoint (typically dump1090-fa's web UI on port 8080), which
    is why this is fetched and merged in separately rather than parsed
    inline with the SBS-1 stream.

    Returns {lowercase_hex_ident: category} for every aircraft that
    reported one -- an aircraft with no category info (most GA aircraft
    with older transponders never report one) is simply omitted, not
    given a fabricated default.
    """
    categories: dict[str, str] = {}
    for aircraft in aircraft_json.get("aircraft", []):
        hex_ident = aircraft.get("hex")
        category = aircraft.get("category")
        if hex_ident and category:
            categories[hex_ident.lower()] = category
    return categories


def fetch_aircraft_categories(url: str, timeout: float = 5.0) -> dict[str, str]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        aircraft_json = json.loads(response.read())
    return parse_aircraft_categories(aircraft_json)


class CategoryLookup:
    """Best-effort, periodically-refreshed hex_ident -> ADS-B emitter
    category cache. "Best-effort" because aircraft.json is genuinely
    optional infrastructure -- a minimal dump1090 install without its web
    server running, a different fork serving it at a different path, or a
    firewalled port all mean this endpoint simply isn't reachable, and
    detections should keep flowing without category enrichment rather
    than the whole bridge failing over a feature that was never the
    critical path (position/altitude, from the SBS-1 stream, always was).
    """

    def __init__(self, url: str | None, refresh_interval_s: float = 15.0):
        self.url = url
        self.refresh_interval_s = refresh_interval_s
        self._categories: dict[str, str] = {}
        # -inf, not 0.0: time.monotonic() isn't epoch-anchored, so on a
        # freshly-booted host its value can itself be smaller than
        # refresh_interval_s, which would silently skip the very first
        # refresh in get() below.
        self._last_refresh = float("-inf")
        self._warned = False

    def get(self, hex_ident: str) -> str | None:
        if self.url is None:
            return None
        now = time.monotonic()
        if now - self._last_refresh >= self.refresh_interval_s:
            self._refresh(now)
        return self._categories.get(hex_ident.lower())

    def _refresh(self, now: float) -> None:
        assert self.url is not None  # only called from get(), which already checked
        self._last_refresh = now
        try:
            self._categories = fetch_aircraft_categories(self.url)
            self._warned = False
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            if not self._warned:
                print(f"WARNING: couldn't fetch aircraft categories from {self.url}: {exc} "
                      "(detections will keep flowing without category enrichment)")
                self._warned = True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sbs-host", default="127.0.0.1", help="dump1090 host")
    parser.add_argument("--sbs-port", type=int, default=30003, help="dump1090 SBS-1 port")
    add_common_post_args(parser, default_sensor_id="dump1090-1")
    parser.add_argument(
        "--aircraft-json-url", default=None,
        help="dump1090-fa/readsb aircraft.json URL for real ADS-B emitter category enrichment "
        "(distinct symbols per aircraft type on the dashboard map) -- defaults to "
        "http://<sbs-host>:8080/data/aircraft.json, dump1090-fa's default web UI location",
    )
    parser.add_argument(
        "--no-category-lookup", action="store_true",
        help="disable aircraft-category enrichment entirely (no aircraft.json fetch attempted)",
    )
    args = parser.parse_args()

    aircraft_json_url = None
    if not args.no_category_lookup:
        aircraft_json_url = args.aircraft_json_url or f"http://{args.sbs_host}:8080/data/aircraft.json"
    category_lookup = CategoryLookup(aircraft_json_url)

    print(f"Connecting to {args.sbs_host}:{args.sbs_port} ...")
    with socket.create_connection((args.sbs_host, args.sbs_port)) as sock:
        print(f"Connected. Posting detections to {args.api_url} as sensor '{args.sensor_id}'.")
        for line in stream_lines(sock):
            payload = parse_sbs1_line(line, sensor_id=args.sensor_id)
            if payload is None:
                continue
            hex_ident = payload["raw_data"]["hex_ident"]
            category = category_lookup.get(hex_ident)
            if category:
                payload["raw_data"]["category"] = category
            try:
                post_detection(
                    args.api_url, payload, args.api_key,
                    max_retries=args.max_retries, retry_backoff_s=args.retry_backoff,
                )
                print(f"-> {payload['latitude']:.5f}, {payload['longitude']:.5f}"
                      + (f" ({category})" if category else ""))
            except urllib.error.URLError as exc:
                print(f"ERROR posting detection: {format_post_error(exc)}")


if __name__ == "__main__":  # pragma: no cover -- only executes when this
    # file is run directly as a script (python -m / a shebang), never when
    # imported under pytest, so it is structurally unreachable in-process;
    # main()'s own body is covered by tests that call it directly.
    main()
