"""Bridges a live microphone array into this tracker's POST /api/detections
endpoint, reporting an actual estimated bearing (via
app/acoustic_beamforming.py's delay-and-sum beamforming) instead of a
single flat manually-estimated confidence number with no direction at
all.

IMPORTANT -- an array alone can't measure RANGE to the source, only
bearing (see app/acoustic_beamforming.py's docstring for why). This
reports azimuth_deg with an *assumed*, operator-supplied --assumed-range-m
rather than a measured one -- the same honest compromise
app/adapters/camera_motion.py makes for a monocular camera's identical
range limitation. Tune it to a realistic detection range for your array
and environment, or better, cross-reference against a second acoustic
array/sensor at a known separate location for a real triangulated
position instead of an assumed range.

Also requires the sensor's mounting position to be registered (see the
README's "Georeferencing" section) -- an azimuth_deg/range_m detection
with no registered sensor position is silently dropped.

Usage:
    pip install -r requirements-acoustic.txt
    .venv/bin/python -m app.adapters.acoustic_array_bridge \\
        --sensor-id acoustic-1 \\
        --mic-positions '[[0.032,0.032],[0.032,-0.032],[-0.032,-0.032],[-0.032,0.032]]' \\
        --assumed-range-m 150 --confidence 0.6
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request
from datetime import UTC, datetime

import numpy as np

from app.acoustic_beamforming import estimate_bearing


def post_detection(url: str, payload: dict, api_key: str = "") -> dict:
    data = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read())


def build_detection_payload(
    azimuth_deg: float,
    bearing_confidence: float,
    sensor_id: str,
    assumed_range_m: float,
    confidence: float,
) -> dict:
    return {
        "sensor_id": sensor_id,
        "sensor_type": "acoustic",
        "timestamp": datetime.now(UTC).replace(tzinfo=None).isoformat(),
        "azimuth_deg": azimuth_deg,
        "range_m": assumed_range_m,
        "confidence": confidence,
        "raw_data": {
            "protocol": "acoustic_beamforming",
            "bearing_confidence": bearing_confidence,
            "range_is_assumed_not_measured": True,
        },
    }


def watch(args: argparse.Namespace) -> None:
    import sounddevice as sd

    mic_positions = json.loads(args.mic_positions)
    n_mics = len(mic_positions)
    block_frames = int(args.block_seconds * args.sample_rate)

    print(
        f"Recording {args.block_seconds}s blocks from {n_mics} mics at {args.sample_rate}Hz, "
        f"posting to {args.api_url} as '{args.sensor_id}' ..."
    )
    while True:
        recording = sd.rec(block_frames, samplerate=args.sample_rate, channels=n_mics, device=args.device)
        sd.wait()
        channels = recording.T  # sounddevice gives (n_samples, n_channels); beamforming wants (n_mics, n_samples)

        azimuth_deg, bearing_confidence = estimate_bearing(
            np.ascontiguousarray(channels), mic_positions, args.sample_rate, args.azimuth_resolution_deg
        )
        payload = build_detection_payload(
            azimuth_deg, bearing_confidence, args.sensor_id, args.assumed_range_m, args.confidence
        )
        try:
            result = post_detection(args.api_url, payload, args.api_key)
            print(
                f"-> azimuth={azimuth_deg:.1f} bearing_confidence={bearing_confidence:.2f} "
                f"track {result.get('track_id')}"
            )
        except urllib.error.URLError as exc:
            print(f"ERROR posting detection: {exc}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sensor-id", default="acoustic-array-1")
    parser.add_argument(
        "--mic-positions", required=True,
        help='JSON list of [x_east_m, y_north_m] mic positions relative to the array center, '
        'e.g. \'[[0.032,0.032],[0.032,-0.032],[-0.032,-0.032],[-0.032,0.032]]\'',
    )
    parser.add_argument("--sample-rate", type=float, default=48000.0)
    parser.add_argument("--block-seconds", type=float, default=0.5, help="Audio window per bearing estimate")
    parser.add_argument("--azimuth-resolution-deg", type=float, default=2.0)
    parser.add_argument(
        "--assumed-range-m", type=float, required=True,
        help="Range is NOT measured by a single array -- this is an operator-supplied assumption",
    )
    parser.add_argument(
        "--confidence", type=float, default=0.6,
        help="Manual estimate of how likely this array's audio source is actually a drone "
        "(separate from bearing_confidence, which reflects only how sharp the DIRECTION estimate is)",
    )
    parser.add_argument("--device", default=None, help="sounddevice input device index or name")
    parser.add_argument("--api-url", default="http://127.0.0.1:8000/api/detections")
    parser.add_argument("--api-key", default=os.getenv("DRONE_API_KEY", ""))
    args = parser.parse_args()
    watch(args)


if __name__ == "__main__":
    main()
