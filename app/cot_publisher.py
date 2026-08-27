"""Optional Cursor on Target (CoT) fan-out: sends a CoT event over UDP for
every track update to a configured TAK endpoint, in addition to (not
instead of) the normal synchronous ingest/fusion path -- the same
additive, best-effort integration pattern as app/queue_publisher.py's NATS
fan-out and app/alerting.py's Slack/PagerDuty/SMS. Disabled entirely
unless DRONE_COT_UDP_HOST is set.
"""

from __future__ import annotations

import logging
import socket

from app.config import COT_STALE_SECONDS, COT_UDP_HOST, COT_UDP_PORT
from app.cot import build_cot_xml
from app.models import Track

logger = logging.getLogger(__name__)


def publish_track_cot(track: Track) -> None:
    if not COT_UDP_HOST:
        return

    payload = build_cot_xml(track, stale_seconds=COT_STALE_SECONDS)
    if payload is None:
        return

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.sendto(payload, (COT_UDP_HOST, COT_UDP_PORT))
    except OSError as exc:
        logger.warning("CoT UDP publish to %s:%s failed: %s", COT_UDP_HOST, COT_UDP_PORT, exc)
