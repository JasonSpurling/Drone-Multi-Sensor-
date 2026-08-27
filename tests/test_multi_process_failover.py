"""Proves the actual claim app/cluster_lock.py's docstring and the
README's "Running multiple replicas behind a load balancer" section make:
that two real app *processes* sharing one PostgreSQL database won't each
independently conclude "no existing track" and create a duplicate for the
same physical object, when detections for it land on different replicas
at close to the same instant.

tests/test_cluster_lock.py already proves the underlying primitive
(pg_advisory_lock actually blocks a second connection) -- this is the
next layer up: that app.tracking's real usage of it holds under a real
concurrent multi-process race, not just that the SQL primitive works in
isolation. Requires DRONE_TEST_DATABASE_URL pointed at PostgreSQL, same
convention as every other PostgreSQL-only test in this suite (skipped
otherwise -- see the README's "Tests" section for how to run this for
real).
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

DETECTION_BASE = {
    "sensor_id": "radar-1", "sensor_type": "radar",
    "latitude": 51.5, "longitude": -0.10001, "confidence": 0.9,
}


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_health(base_url: str, timeout_s: float = 15.0) -> None:
    deadline = time.monotonic() + timeout_s
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{base_url}/api/health", timeout=1) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, ConnectionError) as exc:
            last_error = exc
        time.sleep(0.2)
    raise RuntimeError(f"App server never became healthy at {base_url}") from last_error


@pytest.fixture
def two_replicas(isolated_db):
    """Two real `uvicorn app.main:app` subprocesses, both pointed at the
    same PostgreSQL database -- an actual two-replicas-behind-a-load-
    balancer topology, not a simulation of one.

    isolated_db (autouse, tests/conftest.py) has already reset the schema
    and run init_db() against DRONE_TEST_DATABASE_URL in this (parent)
    process before this fixture runs, so both replica subprocesses' own
    init_db() calls at their startup just find the existing tables --
    no CREATE TABLE race between two processes starting concurrently.
    """
    if isolated_db.dialect.name != "postgresql":
        pytest.skip(
            "Requires DRONE_TEST_DATABASE_URL pointed at PostgreSQL -- "
            "proving cross-process serialization needs a real shared "
            "database two separate processes can actually connect to, "
            "which SQLite (this test's default) isn't."
        )

    env = {
        **os.environ,
        # str(url) masks the password ("***") -- fine for a repr, wrong for
        # something a subprocess actually needs to connect with.
        "DRONE_DATABASE_URL": isolated_db.url.render_as_string(hide_password=False),
    }

    processes = []
    base_urls = []
    try:
        for _ in range(2):
            port = _free_port()
            process = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            base_url = f"http://127.0.0.1:{port}"
            _wait_for_health(base_url)
            processes.append(process)
            base_urls.append(base_url)
        yield base_urls
    finally:
        for process in processes:
            process.terminate()
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()


def test_concurrent_detections_on_different_replicas_do_not_create_duplicate_tracks(two_replicas):
    """The race this actually tests lives at track *creation*: two
    detections for the same not-yet-tracked object, landing on different
    replicas at the same instant, must not both independently conclude
    "no existing track" and each create one. So each round below is a
    fresh, independent bootstrap -- a brand-new location, far outside
    DRONE_TRACK_DISTANCE_GATE_M of every other round's -- with its own
    pair of near-simultaneous "first sighting" detections 1-2 meters
    apart (well inside the gate) sent one to each replica. If
    cluster_association_lock() weren't actually serializing the two
    replicas' association critical sections against each other, this
    reliably produces more than one track for at least one round.
    """
    replica_a, replica_b = two_replicas
    num_rounds = 10

    with ThreadPoolExecutor(max_workers=2) as pool:
        for round_num in range(num_rounds):
            # ~0.02 deg longitude spacing between rounds is ~1.4 km at this
            # latitude -- comfortably outside the default 500 m gate, so
            # each round is an independent object, not a continuation of
            # the previous round's track.
            lon = DETECTION_BASE["longitude"] + round_num * 0.02
            body_a = {**DETECTION_BASE, "longitude": lon}
            body_b = {**DETECTION_BASE, "longitude": lon + 0.00001}  # ~1 m away: same object's other report
            futures = [
                pool.submit(httpx.post, f"{replica_a}/api/detections", json=body_a, timeout=10),
                pool.submit(httpx.post, f"{replica_b}/api/detections", json=body_b, timeout=10),
            ]
            for future in futures:
                assert future.result().status_code == 201

    tracks = httpx.get(f"{replica_a}/api/tracks", timeout=10).json()
    assert len(tracks) == num_rounds, (
        f"expected exactly {num_rounds} tracks (one per round's bootstrap pair), "
        f"got {len(tracks)} -- a race in track creation across replicas would show "
        f"up here as more tracks than rounds"
    )
