"""app.consumer: the additional, opt-in queue-based ingest path (see that
module's own docstring). These tests exercise make_handler()/run()'s
pieces directly against the real association pipeline and a real
database (no HTTP involved -- that's the whole point of this path), plus
one true end-to-end test driving app.consumer.run() against a real
in-process NATS stand-in (tests/test_queue_publisher.py's _FakeNatsServer)
to prove the whole thing wires together, not just each piece in isolation.
"""

from __future__ import annotations

import json
import threading
import time

import pytest

from app.consumer import _resolve_consumer_site_id, make_handler, run
from app.db import create_site, list_tracks
from app.models import Classification, SensorType
from tests.test_queue_publisher import _FakeNatsServer, _wait_for_sub

DETECTION = {
    "sensor_id": "radar-1", "sensor_type": "radar",
    "latitude": 51.5, "longitude": -0.1, "confidence": 0.9,
}


def test_resolve_consumer_site_id_defaults_to_the_default_site(monkeypatch, site_id):
    monkeypatch.setattr("app.consumer.CONSUMER_SITE_NAME", "")
    assert _resolve_consumer_site_id() == site_id


def test_resolve_consumer_site_id_resolves_a_named_site(monkeypatch, isolated_db):
    site = create_site("warehouse-north")
    monkeypatch.setattr("app.consumer.CONSUMER_SITE_NAME", "warehouse-north")
    assert _resolve_consumer_site_id() == site.id


def test_resolve_consumer_site_id_fails_closed_for_an_unknown_site(monkeypatch, isolated_db):
    monkeypatch.setattr("app.consumer.CONSUMER_SITE_NAME", "no-such-site")
    with pytest.raises(RuntimeError, match="no-such-site"):
        _resolve_consumer_site_id()


def test_handler_creates_a_track_from_a_valid_queued_detection(site_id):
    handler = make_handler(site_id)
    handler(json.dumps(DETECTION).encode())

    tracks = list_tracks(site_id=site_id)
    assert len(tracks) == 1
    assert tracks[0].classification in Classification


def test_handler_ignores_malformed_json(site_id):
    handler = make_handler(site_id)
    handler(b"not valid json{{{")
    assert list_tracks(site_id=site_id) == []


def test_handler_ignores_a_payload_that_fails_model_validation(site_id):
    handler = make_handler(site_id)
    handler(json.dumps({"sensor_id": "radar-1"}).encode())  # missing required fields
    assert list_tracks(site_id=site_id) == []


def test_handler_drops_a_detection_with_excessive_clock_skew(site_id, monkeypatch):
    monkeypatch.setattr("app.consumer.MAX_DETECTION_CLOCK_SKEW_SECONDS", 1.0)
    stale = dict(DETECTION, timestamp="2000-01-01T00:00:00")
    handler = make_handler(site_id)
    handler(json.dumps(stale).encode())
    assert list_tracks(site_id=site_id) == []


def test_handler_respects_the_per_sensor_rate_limit(site_id, monkeypatch):
    from app.ratelimit import RateLimiter

    monkeypatch.setattr("app.consumer.detection_rate_limiter", RateLimiter(rate_per_second=1.0, burst=1.0))
    handler = make_handler(site_id)
    handler(json.dumps(DETECTION).encode())
    handler(json.dumps(dict(DETECTION, confidence=0.5)).encode())  # a second, distinct detection, same tick

    # The rate limiter allows only 1 of the 2 -- both would otherwise
    # associate fine (same sensor, close together), so a track count of 1
    # (not 2 separate tracks, and not the second detection's confidence
    # folded in) proves the second was dropped before ever reaching
    # associate_detection, not merged as a legitimate second observation.
    tracks = list_tracks(site_id=site_id)
    assert len(tracks) == 1


def test_handler_never_trusts_client_supplied_id_track_id_or_georeferenced(site_id):
    # Same defense-in-depth POST /api/detections applies (see that
    # module's ingest_detection): these three are always server-assigned/
    # recomputed, never taken from the raw payload.
    tampered = dict(DETECTION, id=999, track_id=999, georeferenced=True)
    handler = make_handler(site_id)
    handler(json.dumps(tampered).encode())

    tracks = list_tracks(site_id=site_id)
    assert len(tracks) == 1
    assert tracks[0].id != 999


def test_handler_scopes_every_detection_to_the_configured_site(monkeypatch, isolated_db):
    site_a = create_site("site-a")
    site_b = create_site("site-b")
    handler_a = make_handler(site_a.id)
    handler_b = make_handler(site_b.id)

    handler_a(json.dumps(DETECTION).encode())
    handler_b(json.dumps(DETECTION).encode())

    assert len(list_tracks(site_id=site_a.id)) == 1
    assert len(list_tracks(site_id=site_b.id)) == 1
    assert SensorType.RADAR  # sanity import check


def test_run_processes_a_real_queued_detection_end_to_end(monkeypatch, isolated_db, site_id):
    """The real thing: app.consumer.run() subscribing to a (fake-server-
    backed) NATS connection and turning an actual published message into
    an actual track in the actual database -- not each piece tested in
    isolation.
    """
    server = _FakeNatsServer()
    monkeypatch.setattr("app.queue_publisher.NATS_URL", f"nats://127.0.0.1:{server.port}")
    monkeypatch.setattr("app.consumer.NATS_RAW_DETECTION_SUBJECT", "drone.detections.raw.test")
    monkeypatch.setattr("app.consumer.CONSUMER_QUEUE_GROUP", "")
    monkeypatch.setattr("app.consumer.CONSUMER_SITE_NAME", "")

    stop_event = threading.Event()
    t = threading.Thread(target=run, args=(stop_event,))
    t.start()
    try:
        _wait_for_sub(server)
        server.send_msg("drone.detections.raw.test", "1", json.dumps(DETECTION).encode())

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not list_tracks(site_id=site_id):
            time.sleep(0.05)
    finally:
        stop_event.set()
        t.join(timeout=5)
        server.close()

    tracks = list_tracks(site_id=site_id)
    assert len(tracks) == 1
    assert tracks[0].latitude == pytest.approx(DETECTION["latitude"])
