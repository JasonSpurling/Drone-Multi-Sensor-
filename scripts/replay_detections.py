"""Record real detection traffic from a running instance and replay it
against another (or the same, later) instance -- useful for reproducing a
tracking/incident bug against a fresh DB, or for feeding a demo/staging
deployment realistic-looking traffic without live sensors attached.

Two steps, because recording and replaying happen at different times
against different servers:

  record   GET /api/detections from a source server over a time window
           (paginated) and save them to a JSONL file, one detection per
           line, in original chronological order.

  replay   POST each recorded detection to a target server's
           /api/detections, in order, with its timestamp rewritten to
           "now" (a stale, already-old timestamp would just be rejected
           by MAX_DETECTION_CLOCK_SKEW_SECONDS -- see
           app/api/detections.py's _check_clock_skew) and its original
           inter-detection spacing preserved (scaled by --speed), so a
           track's replayed motion looks like it did the first time
           instead of arriving all at once.

Usage:
    python scripts/replay_detections.py record --url http://source:8000 \\
        --start 2026-01-01T00:00:00 --end 2026-01-01T01:00:00 \\
        --out captured.jsonl

    python scripts/replay_detections.py replay --url http://target:8000 \\
        --in captured.jsonl --speed 4 --api-key $DRONE_API_KEY
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
import time
from datetime import UTC, datetime, timedelta

import requests

# Server-assigned on ingest (see app/api/detections.py's ingest_detection) --
# stripped before replay so a recorded detection round-trips through POST
# /api/detections exactly like a live sensor's first report would, instead
# of carrying stale identifiers from the source server that mean nothing
# on the target.
_SERVER_ASSIGNED_FIELDS = ("id", "track_id", "site_id", "georeferenced", "human_label")


def fetch_detections(
    base_url: str, start: str | None, end: str | None, sensor_id: str | None,
    api_key: str = "", page_size: int = 500,
) -> list[dict]:
    session = requests.Session()
    if api_key:
        session.headers.update({"X-API-Key": api_key})
    params: dict[str, str | int] = {"limit": page_size, "offset": 0}
    if start:
        params["start"] = start
    if end:
        params["end"] = end
    if sensor_id:
        params["sensor_id"] = sensor_id

    detections: list[dict] = []
    while True:
        response = session.get(f"{base_url}/api/detections", params=params, timeout=30)
        response.raise_for_status()
        page = response.json()
        detections.extend(page)
        if len(page) < page_size:
            break
        params["offset"] = int(params["offset"]) + page_size
    return detections


def save_recorded(path: str, detections: list[dict]) -> None:
    with open(path, "w") as f:
        for detection in detections:
            f.write(json.dumps(detection) + "\n")


def load_recorded(path: str) -> list[dict]:
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def strip_server_fields(detection: dict) -> dict:
    return {k: v for k, v in detection.items() if k not in _SERVER_ASSIGNED_FIELDS}


def rewrite_timestamps(detections: list[dict], replay_start: datetime, speed: float) -> list[dict]:
    """Shifts every detection's timestamp so the first one lands at
    replay_start, preserving the recorded gaps between the rest (scaled by
    speed -- 2.0 replays twice as fast, 0.5 half as fast). Detections are
    assumed already sorted chronologically (GET /api/detections returns
    them in insertion order).
    """
    if not detections:
        return []
    original_start = datetime.fromisoformat(str(detections[0]["timestamp"]))
    rewritten = []
    for detection in detections:
        original_ts = datetime.fromisoformat(str(detection["timestamp"]))
        offset_s = (original_ts - original_start).total_seconds() / speed
        new_ts = replay_start + timedelta(seconds=offset_s)
        rewritten.append({**detection, "timestamp": new_ts.isoformat()})
    return rewritten


def compute_delays_s(detections: list[dict], speed: float) -> list[float]:
    """Per-detection sleep duration before sending it, i.e. the gap since
    the previous detection's original timestamp, scaled by speed. The
    first detection's delay is always 0 (sent immediately).
    """
    delays = [0.0]
    for prev, curr in itertools.pairwise(detections):
        prev_ts = datetime.fromisoformat(str(prev["timestamp"]))
        curr_ts = datetime.fromisoformat(str(curr["timestamp"]))
        delays.append(max(0.0, (curr_ts - prev_ts).total_seconds() / speed))
    return delays


def cmd_record(args: argparse.Namespace) -> None:
    detections = fetch_detections(args.url, args.start, args.end, args.sensor_id, args.api_key)
    save_recorded(args.out, detections)
    print(f"Recorded {len(detections)} detection(s) to {args.out}")


def cmd_replay(args: argparse.Namespace) -> None:
    detections = load_recorded(getattr(args, "in"))
    if not detections:
        print("Nothing to replay: input file has no detections.")
        return
    delays_s = compute_delays_s(detections, args.speed)
    payloads = rewrite_timestamps(detections, datetime.now(UTC).replace(tzinfo=None), args.speed)

    session = requests.Session()
    if args.api_key:
        session.headers.update({"X-API-Key": args.api_key})

    sent, errors = 0, 0
    for payload, delay_s in zip(payloads, delays_s, strict=True):
        if delay_s > 0:
            time.sleep(delay_s)
        body = strip_server_fields(payload)
        try:
            response = session.post(f"{args.url}/api/detections", json=body, timeout=10)
            if response.status_code == 201:
                sent += 1
            else:
                errors += 1
                print(f"  [{response.status_code}] {body.get('sensor_id')}: {response.text[:200]}", file=sys.stderr)
        except requests.RequestException as exc:
            errors += 1
            print(f"  [error] {body.get('sensor_id')}: {exc}", file=sys.stderr)

    print(f"Replayed {sent} detection(s) ({errors} error(s)) to {args.url}")
    if errors and args.fail_on_errors:
        raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_record = subparsers.add_parser("record", help="Save a source server's detections to a JSONL file")
    p_record.add_argument("--url", required=True, help="Source server base URL")
    p_record.add_argument("--start", default=None, help="ISO 8601 start of the time window (inclusive)")
    p_record.add_argument("--end", default=None, help="ISO 8601 end of the time window (inclusive)")
    p_record.add_argument("--sensor-id", default=None, help="Only this sensor's detections")
    p_record.add_argument("--api-key", default="")
    p_record.add_argument("--out", required=True, help="Output JSONL path")
    p_record.set_defaults(func=cmd_record)

    p_replay = subparsers.add_parser("replay", help="POST recorded detections to a target server")
    p_replay.add_argument("--url", required=True, help="Target server base URL")
    p_replay.add_argument("--in", dest="in", required=True, help="Input JSONL path (from `record`)")
    p_replay.add_argument("--speed", type=float, default=1.0, help="Playback speed multiplier (2.0 = 2x faster)")
    p_replay.add_argument("--api-key", default="")
    p_replay.add_argument(
        "--fail-on-errors", action="store_true", help="Exit 1 if any detection failed to POST (for CI/scripting)"
    )
    p_replay.set_defaults(func=cmd_replay)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
