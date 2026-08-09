"""Posts realistic detections to a running API for manual/integration testing.

Simulates three moving entities:
  - a drone flying a straight line into the seeded restricted zone
  - an ADS-B aircraft cruising well outside it
  - a low-confidence camera return wandering nearby (likely a bird)

Usage:
    python simulator.py
    python simulator.py --url http://127.0.0.1:8000/api/detections --ticks 20 --interval 0.5
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone

DEFAULT_URL = "http://127.0.0.1:8000/api/detections"
_METERS_PER_DEGREE_LAT = 111_320.0


@dataclass
class SimulatedEntity:
    sensor_id: str
    sensor_type: str
    lat: float
    lon: float
    alt_m: float
    heading_deg: float
    speed_mps: float
    confidence_base: float
    confidence_jitter: float = 0.05

    def step(self, dt_s: float) -> None:
        distance_m = self.speed_mps * dt_s
        d_lat = (distance_m * math.cos(math.radians(self.heading_deg))) / _METERS_PER_DEGREE_LAT
        meters_per_degree_lon = _METERS_PER_DEGREE_LAT * math.cos(math.radians(self.lat))
        d_lon = (distance_m * math.sin(math.radians(self.heading_deg))) / meters_per_degree_lon
        self.lat += d_lat
        self.lon += d_lon

    def detection_payload(self) -> dict:
        confidence = max(0.0, min(1.0, random.gauss(self.confidence_base, self.confidence_jitter)))
        return {
            "sensor_id": self.sensor_id,
            "sensor_type": self.sensor_type,
            "timestamp": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
            "latitude": round(self.lat, 6),
            "longitude": round(self.lon, 6),
            "altitude_m": round(self.alt_m, 1),
            "confidence": round(confidence, 3),
        }


def build_entities() -> list[SimulatedEntity]:
    return [
        # Starts just southwest of the seeded restricted zone and flies
        # northeast into it (crosses the boundary in ~20s at default settings).
        SimulatedEntity(
            sensor_id="sim-radar-1", sensor_type="radar",
            lat=51.489, lon=-0.111, alt_m=90,
            heading_deg=45, speed_mps=8, confidence_base=0.9,
        ),
        # Cruises well clear of the zone.
        SimulatedEntity(
            sensor_id="sim-adsb-1", sensor_type="adsb",
            lat=51.60, lon=-0.30, alt_m=3200,
            heading_deg=200, speed_mps=210, confidence_base=0.98,
        ),
        # Wanders slowly nearby with low confidence -> classified as a bird.
        SimulatedEntity(
            sensor_id="sim-cam-1", sensor_type="camera",
            lat=51.472, lon=-0.155, alt_m=25,
            heading_deg=random.uniform(0, 360), speed_mps=2, confidence_base=0.25,
        ),
    ]


def post_detection(url: str, payload: dict, api_key: str = "") -> dict:
    data = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default=DEFAULT_URL, help="Detections endpoint URL")
    parser.add_argument("--ticks", type=int, default=30, help="Number of simulation ticks")
    parser.add_argument("--interval", type=float, default=1.0, help="Seconds between ticks")
    parser.add_argument("--seed", type=int, default=None, help="Random seed for reproducibility")
    parser.add_argument(
        "--api-key", default=os.getenv("DRONE_API_KEY", ""),
        help="X-API-Key header value; defaults to $DRONE_API_KEY, only needed if the server has one set",
    )
    args = parser.parse_args()

    if args.seed is not None:
        random.seed(args.seed)

    entities = build_entities()
    for tick in range(1, args.ticks + 1):
        for entity in entities:
            entity.step(args.interval)
            payload = entity.detection_payload()
            try:
                result = post_detection(args.url, payload, args.api_key)
                print(
                    f"[tick {tick:>3}] {entity.sensor_id:<12} -> track {result['track_id']} "
                    f"({payload['latitude']:.5f}, {payload['longitude']:.5f}) "
                    f"conf={payload['confidence']}"
                )
            except urllib.error.URLError as exc:
                print(f"[tick {tick:>3}] {entity.sensor_id:<12} -> ERROR: {exc}")
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
