"""Reads hackrf_sweep's CSV output from stdin and posts an energy-
detection alert to /api/detections whenever a swept bin exceeds the
sweep's own noise floor by --threshold-db -- see app/adapters/rf_sweep.py
for what this does and doesn't tell you (presence only, no protocol
identification, no direction/range).

This bridge itself has no SDR/hardware dependency -- it's a plain
CSV-lines stdin consumer, decoupled from the actual HackRF entirely (the
same shape as app/adapters/dji_droneid_bridge.py's stdin-piped design).
Install hackrf-tools separately (apt install hackrf, or build from
https://github.com/greatscottgadgets/hackrf) and pipe its output in:

Usage:
    hackrf_sweep -f 2400:2500,5725:5875 -w 600000 \\
        | .venv/bin/python -m app.adapters.rf_sweep_bridge \\
            --sensor-id rf-sweep-1 --target-lat 51.5 --target-lon -0.1

Recording a quiet-band baseline first (a session known to have no drone
present) and pointing --baseline-csv at it uses a fixed reference noise
floor instead of trusting each live sweep's own median, which a site
with persistent in-band RF activity (a permanently-on WiFi AP, say)
could otherwise skew:
    hackrf_sweep -f 2400:2500,5725:5875 -w 600000 > control.csv
    # (Ctrl-C after ~1 minute with no drone present)
"""

from __future__ import annotations

import argparse
import sys
import time
import urllib.error

from app.adapters.rf_sweep import (
    bins_from_segment,
    build_detection_payload,
    estimate_noise_floor_db,
    find_peaks,
    parse_sweep_line,
)
from app.adapters.sdk import add_common_post_args, format_post_error, post_detection


def load_baseline_noise_floor_db(path: str) -> float:
    """Reads a previously recorded quiet-band hackrf_sweep CSV (see the
    module docstring) and returns its overall median power -- a fixed
    reference noise floor from a moment known to have no drone present.
    """
    all_bins: list[tuple[float, float]] = []
    with open(path) as f:
        for line in f:
            segment = parse_sweep_line(line)
            if segment is not None:
                all_bins.extend(bins_from_segment(segment))
    if not all_bins:
        raise SystemExit(f"--baseline-csv {path!r} contained no parseable hackrf_sweep lines")
    return estimate_noise_floor_db(all_bins)


def watch(args: argparse.Namespace) -> None:
    baseline_noise_floor_db = load_baseline_noise_floor_db(args.baseline_csv) if args.baseline_csv else None
    if baseline_noise_floor_db is not None:
        print(f"Using fixed baseline noise floor from {args.baseline_csv}: {baseline_noise_floor_db:.1f} dB")

    pending_timestamp: tuple[str, str] | None = None
    pending_bins: list[tuple[float, float]] = []
    last_post: dict[int, float] = {}  # rounded frequency bucket -> last post monotonic time

    def _process_sweep(bins: list[tuple[float, float]]) -> None:
        if not bins:
            return
        noise_floor_db = (
            baseline_noise_floor_db if baseline_noise_floor_db is not None else estimate_noise_floor_db(bins)
        )
        peaks = find_peaks(bins, noise_floor_db, args.threshold_db)
        now = time.monotonic()
        for frequency_hz, power_db in peaks:
            bucket = round(frequency_hz / args.min_interval_bucket_hz)
            if now - last_post.get(bucket, 0.0) < args.min_interval:
                continue
            payload = build_detection_payload(
                frequency_hz, power_db, noise_floor_db, args.sensor_id, args.target_lat, args.target_lon,
                args.confidence,
            )
            try:
                result = post_detection(
                    args.api_url, payload, args.api_key,
                    max_retries=args.max_retries, retry_backoff_s=args.retry_backoff,
                )
                print(
                    f"-> {frequency_hz / 1e6:.3f}MHz power={power_db:.1f}dB "
                    f"(+{power_db - noise_floor_db:.1f} over noise floor) track {result.get('track_id')}"
                )
                last_post[bucket] = now
            except urllib.error.URLError as exc:
                print(f"ERROR posting detection: {format_post_error(exc)}")

    print(f"Reading hackrf_sweep CSV from stdin, posting to {args.api_url} as '{args.sensor_id}' ...")
    for line in sys.stdin:
        segment = parse_sweep_line(line)
        if segment is None:
            continue
        if pending_timestamp is not None and segment.timestamp_key != pending_timestamp:
            _process_sweep(pending_bins)
            pending_bins = []
        pending_timestamp = segment.timestamp_key
        pending_bins.extend(bins_from_segment(segment))
    _process_sweep(pending_bins)  # flush whatever's left when stdin closes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_common_post_args(parser, default_sensor_id="rf-sweep-1")
    parser.add_argument(
        "--target-lat", type=float, required=True,
        help="Latitude to report every detection at -- this sensor has no bearing/range, see module docstring",
    )
    parser.add_argument("--target-lon", type=float, required=True)
    parser.add_argument(
        "--threshold-db", type=float, default=15.0, help="dB above the noise floor a bin must exceed to be flagged"
    )
    parser.add_argument(
        "--baseline-csv", default=None,
        help="Path to a pre-recorded quiet-band hackrf_sweep CSV (see module docstring) -- uses its median "
        "as a fixed noise floor instead of each live sweep's own",
    )
    parser.add_argument(
        "--confidence", type=float, default=0.4,
        help="Manual estimate that a flagged emission is actually a drone -- this sensor can't identify "
        "what's transmitting, only that something is (see module docstring)",
    )
    parser.add_argument(
        "--min-interval", type=float, default=30.0,
        help="Seconds between repeated posts for the same frequency bucket",
    )
    parser.add_argument(
        "--min-interval-bucket-hz", type=float, default=5_000_000.0,
        help="Frequency bucket width (Hz) for --min-interval de-duplication",
    )
    args = parser.parse_args()
    watch(args)


if __name__ == "__main__":
    main()
