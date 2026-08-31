"""Shared plumbing for the real-sensor bridges in this package (see
app/adapters/__init__.py) -- every one of them independently duplicates
the same three things: a urllib POST to /api/detections with an optional
X-API-Key header, a --api-url/--api-key/--sensor-id argparse block, and a
try/except around urllib.error.URLError that just prints and carries on.
This module is that shared code, extracted once real duplication existed
across sbs1_bridge.py, camera_motion.py, mavlink_bridge.py, etc. -- not
speculative, and existing adapters aren't all migrated onto it in one
pass (see camera_motion.py for the first one that is); each adapter's own
watch loop (blocking recv, frame capture, polling) is too different to
share, so this only covers the POST call and the arg wiring, not the
whole "loop forever posting detections" shape.

A new adapter should:
    from app.adapters.sdk import add_common_post_args, post_detection

    parser = argparse.ArgumentParser(...)
    add_common_post_args(parser, default_sensor_id="my-sensor-1")
    ...
    result = post_detection(args.api_url, payload, args.api_key,
                             max_retries=args.max_retries, retry_backoff_s=args.retry_backoff)
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request


def post_detection(
    url: str, payload: dict, api_key: str = "", timeout: float = 5.0,
    max_retries: int = 0, retry_backoff_s: float = 1.0,
) -> dict:
    """POSTs one detection payload and returns the decoded JSON response.
    max_retries=0 (the default) matches every existing adapter's original
    behavior exactly: one attempt, raise on failure, let the caller's own
    loop decide what to do next (usually: print and move on to the next
    reading, per adapter's watch()). Pass max_retries>0 for a transient
    network blip an adapter would rather ride out itself -- exponential
    backoff (retry_backoff_s, then x2, x4, ...) between attempts.
    """
    data = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")

    attempt = 0
    while True:
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read())
        except urllib.error.URLError:
            if attempt >= max_retries:
                raise
            time.sleep(retry_backoff_s * (2**attempt))
            attempt += 1


def add_common_post_args(
    parser: argparse.ArgumentParser, default_sensor_id: str,
    default_api_url: str = "http://127.0.0.1:8000/api/detections",
) -> None:
    """Adds the --api-url/--api-key/--sensor-id/--max-retries/
    --retry-backoff arguments every adapter needs, wired the same way
    (DRONE_API_KEY env var as the --api-key default, same as every
    existing adapter already does by hand).
    """
    parser.add_argument("--api-url", default=default_api_url)
    parser.add_argument("--sensor-id", default=default_sensor_id)
    parser.add_argument("--api-key", default=os.getenv("DRONE_API_KEY", ""))
    parser.add_argument(
        "--max-retries", type=int, default=0,
        help="Retry a failed POST this many times (exponential backoff) before giving up on that detection",
    )
    parser.add_argument("--retry-backoff", type=float, default=1.0, dest="retry_backoff", help=argparse.SUPPRESS)
