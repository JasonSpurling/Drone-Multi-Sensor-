"""Optional message-queue fan-out: publishes a JSON copy of every ingested
detection and opened incident onto a NATS core subject, in addition to (not
instead of) the normal synchronous single-process ingest/fusion path.

This exists for the "if you ever need multi-site or high-throughput
deployment" case flagged in the operational-maturity roadmap: the current
design -- one process handling ingest, association, and fusion inline in
the request thread -- is fine for a single site's real-time load, and this
publish call doesn't change that. What it gives a future scale-out
deployment is a place to plug in: a consumer process (or several, at
another site) can subscribe to these subjects and build a second read path
-- fan-in aggregation, a separate analytics pipeline, cross-site
correlation -- without touching the ingest API or the tracker itself. It
does NOT make the ingest/fusion pipeline queue-based; that would be a
genuine architecture change (an async consumer replacing the synchronous
call chain in app/tracking.py) that this deliberately does not attempt.

Implemented as a minimal NATS core PUB client over a raw socket (the NATS
text protocol is simple enough not to need the official async-only client
library, which would also drag asyncio into an otherwise synchronous
codebase) rather than pulling in Kafka, whose client needs a native
librdkafka build. Disabled entirely unless DRONE_NATS_URL is set.
"""

from __future__ import annotations

import json
import logging
import socket
from urllib.parse import urlparse

from app.config import (
    NATS_CONNECT_TIMEOUT_SECONDS,
    NATS_DETECTION_SUBJECT,
    NATS_INCIDENT_SUBJECT,
    NATS_URL,
)

logger = logging.getLogger(__name__)

_DEFAULT_NATS_PORT = 4222


def _parse_nats_url(url: str) -> tuple[str, int]:
    parsed = urlparse(url)
    return parsed.hostname or "127.0.0.1", parsed.port or _DEFAULT_NATS_PORT


def _read_line(sock: socket.socket) -> bytes:
    buffer = b""
    while not buffer.endswith(b"\r\n"):
        chunk = sock.recv(1)
        if not chunk:
            break
        buffer += chunk
    return buffer


def publish(subject: str, payload: dict) -> None:
    """Best-effort NATS core PUB of `payload` (as JSON) to `subject`. A
    no-op if DRONE_NATS_URL isn't configured; any connection or protocol
    failure is logged and swallowed -- same tradeoff as the webhook and
    alerting integrations, a dead or unreachable broker must not be able
    to stall detection ingestion or incident handling.
    """
    if not NATS_URL:
        return

    host, port = _parse_nats_url(NATS_URL)
    data = json.dumps(payload).encode()
    try:
        with socket.create_connection((host, port), timeout=NATS_CONNECT_TIMEOUT_SECONDS) as sock:
            sock.settimeout(NATS_CONNECT_TIMEOUT_SECONDS)
            _read_line(sock)  # server's INFO greeting
            sock.sendall(
                b'CONNECT {"verbose":false,"pedantic":false,"tls_required":false,'
                b'"name":"drone-multi-sensor"}\r\n'
            )
            sock.sendall(f"PUB {subject} {len(data)}\r\n".encode() + data + b"\r\n")
    except (OSError, socket.timeout) as exc:
        logger.warning("NATS publish to %s failed: %s", subject, exc)


def publish_detection(payload: dict) -> None:
    publish(NATS_DETECTION_SUBJECT, payload)


def publish_incident(payload: dict) -> None:
    publish(NATS_INCIDENT_SUBJECT, payload)
