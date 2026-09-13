"""An additional, opt-in queue-based ingest path -- run as its own process
(`python -m app.consumer`), subscribing to DRONE_NATS_RAW_DETECTION_SUBJECT
over NATS and running the exact same association/fusion/incident pipeline
(app.tracking.associate_detection) POST /api/detections already runs
inline, just triggered by a queue message instead of an HTTP request.

This does NOT replace or change POST /api/detections in any way -- that
stays exactly as it is today, including its response contract (a
persisted, already-associated Detection returned synchronously). This is
a second, additional front door into the same tracking pipeline, for a
deployment that specifically wants ingest processing decoupled from (and
independently scalable from) the API process: publish raw detection JSON
onto DRONE_NATS_RAW_DETECTION_SUBJECT from wherever your sensors' data
actually originates, and run one or more `python -m app.consumer`
processes -- sharing DRONE_CONSUMER_QUEUE_GROUP load-balances the work
across them (each raw detection goes to exactly one consumer, not every
one of them), the actual mechanism a horizontally-scaled worker pool
needs. See app/queue_publisher.py's consume() for the NATS mechanics.

Every detection this consumer processes is stamped with
DRONE_CONSUMER_SITE_NAME's site (the default site if unset) -- there's no
per-message credential to derive a site from the way an API key's "site"
field provides one for the HTTP path (whoever can publish to the
configured NATS subject is implicitly as trusted as an ingest-role API
key would be), so run a separate consumer (against a separate subject, if
needed) per site rather than trying to pack multiple sites' detections
onto one subject.
"""

from __future__ import annotations

import argparse
import json
import logging
import signal
import threading

from pydantic import ValidationError

from app.config import (
    CONSUMER_QUEUE_GROUP,
    CONSUMER_SITE_NAME,
    MAX_DETECTION_CLOCK_SKEW_SECONDS,
    NATS_RAW_DETECTION_SUBJECT,
)
from app.metrics import (
    clock_skew_rejected_total,
    detections_ingested_total,
    rate_limited_total,
    site_rate_limited_total,
)
from app.models import Detection
from app.queue_publisher import consume
from app.ratelimit import detection_rate_limiter, site_detection_rate_limiter
from app.tracking import associate_detection
from app.util import utcnow

logger = logging.getLogger(__name__)


def _resolve_consumer_site_id() -> int:
    if not CONSUMER_SITE_NAME:
        from app.sites import ensure_default_site

        return ensure_default_site()

    from app.db import get_site_by_name

    site = get_site_by_name(CONSUMER_SITE_NAME)
    if site is None:
        raise RuntimeError(
            f"DRONE_CONSUMER_SITE_NAME='{CONSUMER_SITE_NAME}' does not match any configured site"
        )
    assert site.id is not None
    return site.id


def _passes_clock_skew(detection: Detection) -> bool:
    """Same check POST /api/detections applies (see that module's
    _check_clock_skew) -- duplicated rather than shared, since the HTTP
    handler's version raises HTTPException, which has no meaning for a
    non-HTTP consumer; both compare against the same
    MAX_DETECTION_CLOCK_SKEW_SECONDS config value.
    """
    skew_s = abs((utcnow() - detection.timestamp).total_seconds())
    if skew_s > MAX_DETECTION_CLOCK_SKEW_SECONDS:
        clock_skew_rejected_total.labels(sensor_id=detection.sensor_id).inc()
        logger.warning(
            "Dropping queued detection from sensor '%s': timestamp %.0fs from server clock "
            "(limit %.0fs)", detection.sensor_id, skew_s, MAX_DETECTION_CLOCK_SKEW_SECONDS,
        )
        return False
    return True


def _passes_rate_limit(sensor_id: str, site_id: int) -> bool:
    if not detection_rate_limiter.allow(sensor_id):
        rate_limited_total.labels(sensor_id=sensor_id).inc()
        logger.warning("Dropping queued detection from sensor '%s': per-sensor rate limit exceeded", sensor_id)
        return False
    site_key = str(site_id)
    if not site_detection_rate_limiter.allow(site_key):
        site_rate_limited_total.labels(site_id=site_key).inc()
        logger.warning("Dropping queued detection: site-wide rate limit exceeded for site_id=%s", site_id)
        return False
    return True


def make_handler(site_id: int):
    """Builds the per-message handler consume() calls -- a closure over
    `site_id` (resolved once at process startup, not per message) so
    every detection this consumer processes is scoped the same way.
    """

    def handle(payload: bytes) -> None:
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            logger.warning("Dropping queued detection: not valid JSON (%d bytes)", len(payload))
            return
        try:
            detection = Detection.model_validate(data)
        except ValidationError as exc:
            logger.warning("Dropping queued detection: failed validation: %s", exc)
            return

        if not _passes_clock_skew(detection):
            return
        if not _passes_rate_limit(detection.sensor_id, site_id):
            return

        detections_ingested_total.labels(sensor_type=detection.sensor_type.value).inc()
        # Same reasoning as POST /api/detections: never trust these three
        # from the raw payload -- id/track_id are server-assigned,
        # georeferenced must be recomputed server-side so a signed
        # detection's signature verifies against a position it actually
        # signed (see app/georeference.py).
        detection.id = None
        detection.track_id = None
        detection.georeferenced = False
        detection.site_id = site_id
        try:
            associate_detection(detection)
        except Exception:
            logger.exception("Failed to process queued detection from sensor '%s'", detection.sensor_id)

    return handle


def run(stop_event: threading.Event | None = None) -> None:
    from app.db import init_db

    init_db()
    site_id = _resolve_consumer_site_id()
    logger.info(
        "Starting drone-multi-sensor consumer: subject='%s' queue_group='%s' site_id=%d",
        NATS_RAW_DETECTION_SUBJECT, CONSUMER_QUEUE_GROUP, site_id,
    )
    consume(
        NATS_RAW_DETECTION_SUBJECT,
        make_handler(site_id),
        queue_group=CONSUMER_QUEUE_GROUP,
        stop_event=stop_event,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.parse_args()

    from app.logging_config import configure_logging

    configure_logging()

    stop_event = threading.Event()

    def _handle_signal(signum, frame) -> None:
        logger.info("Received signal %d -- shutting down", signum)
        stop_event.set()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    run(stop_event)
    logger.info("Consumer stopped")


if __name__ == "__main__":  # pragma: no cover -- only executes when this
    # file is run directly as a script (python -m / a shebang), never when
    # imported under pytest, so it is structurally unreachable in-process;
    # main()'s own body is covered by tests that call it directly.
    main()
