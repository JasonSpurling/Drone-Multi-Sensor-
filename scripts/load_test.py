"""Throughput/latency load test against a running instance of this app.

Not part of the pytest suite or CI: a load test's numbers are a property
of the machine running it (CPU, disk, DB backend), not a pass/fail
correctness check -- what's useful is a documented, reproducible way to
generate real numbers, not a hardcoded threshold that would be meaningless
on different hardware. See README.md's "Load testing" section for the
last-recorded results and how to reproduce them.

Two things are measured, because they stress different parts of the
tracking pipeline:

  single    Repeated POST /api/detections from many concurrent "sensors"
            (threads) -- the realistic shape of several independent radars/
            cameras/RF sensors reporting on their own schedule. Serializes
            through app.tracking's _association_lock (one process-wide
            lock around the whole gate-then-write sequence), so this is
            what actually measures that lock's throughput ceiling.

  batch     Repeated POST /api/detections/batch with N simultaneous plots
            per call -- one radar scan/sweep's shape, exercising the
            Hungarian-assignment joint-resolution path instead of greedy
            single-detection association.

Usage:
    python scripts/load_test.py --url http://127.0.0.1:8000 --mode single \\
        --sensors 20 --duration 15
    python scripts/load_test.py --url http://127.0.0.1:8000 --mode batch \\
        --batch-size 30 --requests 200
"""

from __future__ import annotations

import argparse
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime

import requests


def _detection_payload(sensor_id: str, lat: float, lon: float, confidence: float = 0.85) -> dict:
    return {
        "sensor_id": sensor_id,
        "sensor_type": "radar",
        "timestamp": datetime.now(UTC).replace(tzinfo=None).isoformat(),
        "latitude": lat,
        "longitude": lon,
        "confidence": confidence,
    }


class _Result:
    __slots__ = ("errors", "hard_errors", "latencies_s", "lock")

    def __init__(self) -> None:
        self.latencies_s: list[float] = []
        self.errors = 0
        # A subset of errors: server-side failures (5xx, or the request
        # never completed at all) -- not a 4xx like 429 (Too Many
        # Requests), which is the rate limiter correctly doing its job
        # under intentionally saturating load, not a bug. --fail-on-errors
        # (see main()) gates on this, not `errors`, for exactly that
        # reason: a load-smoke CI job should catch a deadlock or an
        # unhandled exception under concurrency, not flag the rate limiter
        # working as designed.
        self.hard_errors = 0
        self.lock = threading.Lock()

    def record(self, latency_s: float, ok: bool, hard_error: bool = False) -> None:
        with self.lock:
            self.latencies_s.append(latency_s)
            if not ok:
                self.errors += 1
            if hard_error:
                self.hard_errors += 1


def _report(label: str, result: _Result, wall_time_s: float) -> None:
    n = len(result.latencies_s)
    if n == 0:
        print(f"{label}: no requests completed")
        return
    sorted_lat = sorted(result.latencies_s)
    p50 = sorted_lat[int(n * 0.50)]
    p95 = sorted_lat[min(int(n * 0.95), n - 1)]
    p99 = sorted_lat[min(int(n * 0.99), n - 1)]
    print(f"\n=== {label} ===")
    print(f"requests:        {n} ({result.errors} errors)")
    print(f"wall time:       {wall_time_s:.2f}s")
    print(f"throughput:      {n / wall_time_s:.1f} req/s")
    print(f"latency p50/p95/p99: {p50*1000:.1f}ms / {p95*1000:.1f}ms / {p99*1000:.1f}ms")
    print(f"latency mean/max:    {statistics.mean(sorted_lat)*1000:.1f}ms / {sorted_lat[-1]*1000:.1f}ms")


def run_single(base_url: str, num_sensors: int, duration_s: float, api_key: str = "") -> int:
    """Each of num_sensors threads posts single detections back-to-back
    (no think time) for duration_s -- an intentionally saturating load to
    find the ceiling, not a realistic sensor's real-world duty cycle.
    """
    session = requests.Session()
    if api_key:
        session.headers.update({"X-API-Key": api_key})
    result = _Result()
    stop_at = time.monotonic() + duration_s

    def worker(sensor_index: int) -> None:
        sensor_id = f"load-test-radar-{sensor_index}"
        lat, lon = 51.5 + sensor_index * 0.01, -0.1
        while time.monotonic() < stop_at:
            lat += 0.00001
            payload = _detection_payload(sensor_id, lat, lon)
            start = time.monotonic()
            try:
                r = session.post(f"{base_url}/api/detections", json=payload, timeout=10)
                ok = r.status_code == 201
                hard_error = r.status_code >= 500
            except requests.RequestException:
                ok = False
                hard_error = True
            result.record(time.monotonic() - start, ok, hard_error)

    start_wall = time.monotonic()
    with ThreadPoolExecutor(max_workers=num_sensors) as pool:
        futures = [pool.submit(worker, i) for i in range(num_sensors)]
        for f in as_completed(futures):
            f.result()
    wall_time_s = time.monotonic() - start_wall

    _report(f"single-detection ingest ({num_sensors} concurrent sensors, {duration_s}s)", result, wall_time_s)
    return result.hard_errors


def run_batch(base_url: str, batch_size: int, num_requests: int, api_key: str = "") -> int:
    """Repeated POST /api/detections/batch, each with batch_size
    simultaneous plots (one radar sweep's shape) -- sequential, not
    concurrent, since a single sensor's own sweeps arrive one at a time.
    """
    session = requests.Session()
    if api_key:
        session.headers.update({"X-API-Key": api_key})
    result = _Result()
    lat, lon = 51.5, -0.1

    start_wall = time.monotonic()
    for _ in range(num_requests):
        lat += 0.00002
        payload = [
            _detection_payload("load-test-batch-radar", lat + i * 0.0005, lon)
            for i in range(batch_size)
        ]
        start = time.monotonic()
        try:
            r = session.post(f"{base_url}/api/detections/batch", json=payload, timeout=30)
            ok = r.status_code == 201
            hard_error = r.status_code >= 500
        except requests.RequestException:
            ok = False
            hard_error = True
        result.record(time.monotonic() - start, ok, hard_error)
    wall_time_s = time.monotonic() - start_wall

    _report(f"batch ingest ({batch_size} plots/request x {num_requests} requests)", result, wall_time_s)
    if result.latencies_s:
        plots_per_s = (batch_size * num_requests) / wall_time_s
        print(f"plots/s (batch_size * requests / wall time): {plots_per_s:.1f}")
    return result.hard_errors


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--api-key", default="")
    parser.add_argument("--mode", choices=["single", "batch", "both"], default="both")
    parser.add_argument("--sensors", type=int, default=20, help="[single] concurrent sensor threads")
    parser.add_argument("--duration", type=float, default=15.0, help="[single] seconds to sustain load")
    parser.add_argument("--batch-size", type=int, default=30, help="[batch] plots per batch request")
    parser.add_argument("--requests", type=int, default=200, help="[batch] number of batch requests")
    parser.add_argument(
        "--fail-on-errors",
        action="store_true",
        help=(
            "Exit 1 if any request errored (non-201 or a connection failure). Off by "
            "default -- this script's normal use is generating throughput/latency "
            "numbers to read, where an isolated error or two isn't a run failure. "
            "Set for CI's load-smoke job (see .github/workflows/tests.yml), where the "
            "point isn't the throughput number (meaningless on a shared runner -- see "
            "this file's module docstring) but whether concurrent load trips a real "
            "bug (a deadlock, an unhandled exception, a race in detection "
            "association) that only shows up under concurrency, not in the single-"
            "request-at-a-time pytest suite."
        ),
    )
    args = parser.parse_args()

    total_hard_errors = 0
    if args.mode in ("single", "both"):
        total_hard_errors += run_single(args.url, args.sensors, args.duration, args.api_key)
    if args.mode in ("batch", "both"):
        total_hard_errors += run_batch(args.url, args.batch_size, args.requests, args.api_key)

    if args.fail_on_errors and total_hard_errors > 0:
        print(f"\n{total_hard_errors} request(s) hard-errored -- failing (--fail-on-errors set).")
        sys.exit(1)


if __name__ == "__main__":
    main()
