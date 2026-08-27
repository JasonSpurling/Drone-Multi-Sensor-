"""Optional message-queue fan-out (publish()/publish_detection()/
publish_incident()) and consume (consume(), for app/consumer.py) over a
NATS core connection, in addition to (not instead of) the normal
synchronous single-process ingest/fusion path POST /api/detections uses.

Publishing exists for the "if you ever need multi-site or high-throughput
deployment" case flagged in the operational-maturity roadmap: the default
design -- one process handling ingest, association, and fusion inline in
the request thread -- is fine for a single site's real-time load, and this
publish call doesn't change that. What it gives a scale-out deployment is
a place to plug in: a consumer process (or several, at another site) can
subscribe to these subjects and build a second read path -- fan-in
aggregation, a separate analytics pipeline, cross-site correlation --
without touching the ingest API or the tracker itself.

consume() is the other half, letting app/consumer.py be that subscriber --
see that module for the actual opt-in queue-based ingest path it builds on
top of this. Both directions share one minimal NATS core client over a raw
socket (the NATS text protocol is simple enough not to need the official
async-only client library, which would also drag asyncio into an otherwise
synchronous codebase) rather than pulling in Kafka, whose client needs a
native librdkafka build. Disabled entirely unless DRONE_NATS_URL is set.
"""

from __future__ import annotations

import json
import logging
import socket
import threading
from collections.abc import Callable
from urllib.parse import urlparse

from app.config import (
    NATS_CONNECT_TIMEOUT_SECONDS,
    NATS_DETECTION_SUBJECT,
    NATS_INCIDENT_SUBJECT,
    NATS_URL,
)

logger = logging.getLogger(__name__)

_DEFAULT_NATS_PORT = 4222
# How often the receive loop wakes up even with nothing to read -- bounds
# how long consume() can take to notice stop_event was set (a slow
# shutdown, not a correctness issue: no message is lost either way, the
# socket is simply polled at this granularity instead of blocking forever).
_RECV_POLL_TIMEOUT_SECONDS = 1.0


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
    except (TimeoutError, OSError) as exc:
        logger.warning("NATS publish to %s failed: %s", subject, exc)


def publish_detection(payload: dict) -> None:
    publish(NATS_DETECTION_SUBJECT, payload)


def publish_incident(payload: dict) -> None:
    publish(NATS_INCIDENT_SUBJECT, payload)


def _subscribe_once(
    subject: str, queue_group: str, handler: Callable[[bytes], None], stop_event: threading.Event
) -> None:
    """One connection's worth of SUB + receive loop. Raises on any
    connection/protocol problem (including simply running out of retries
    on a malformed frame) so consume() below can log it and reconnect --
    a single bad frame or a mid-stream disconnect must not permanently
    kill the consumer process.
    """
    host, port = _parse_nats_url(NATS_URL)
    with socket.create_connection((host, port), timeout=NATS_CONNECT_TIMEOUT_SECONDS) as sock:
        sock.settimeout(_RECV_POLL_TIMEOUT_SECONDS)
        _read_line(sock)  # server's INFO greeting
        sock.sendall(
            b'CONNECT {"verbose":false,"pedantic":false,"tls_required":false,'
            b'"name":"drone-multi-sensor-consumer"}\r\n'
        )
        sub_line = f"SUB {subject} {queue_group} 1\r\n" if queue_group else f"SUB {subject} 1\r\n"
        sock.sendall(sub_line.encode())
        logger.info("Subscribed to NATS subject '%s' (queue group '%s')", subject, queue_group or "-")

        buffer = b""
        while not stop_event.is_set():
            try:
                chunk = sock.recv(65536)
            except TimeoutError:
                continue
            if not chunk:
                raise ConnectionError("NATS connection closed by server")
            buffer += chunk

            while True:
                newline_at = buffer.find(b"\r\n")
                if newline_at == -1:
                    break
                line, rest = buffer[:newline_at], buffer[newline_at + 2 :]

                if line.startswith(b"MSG "):
                    # MSG <subject> <sid> [reply-to] <#bytes>
                    n_bytes = int(line.split()[-1])
                    while len(rest) < n_bytes + 2:
                        try:
                            more = sock.recv(65536)
                        except TimeoutError:
                            if stop_event.is_set():
                                return
                            continue
                        if not more:
                            raise ConnectionError("NATS connection closed mid-message")
                        rest += more
                    payload, buffer = rest[:n_bytes], rest[n_bytes + 2 :]
                    handler(payload)
                elif line.startswith(b"PING"):
                    sock.sendall(b"PONG\r\n")
                    buffer = rest
                else:
                    # +OK/-ERR/anything else this minimal client doesn't
                    # act on -- just move past it.
                    buffer = rest


def consume(
    subject: str,
    handler: Callable[[bytes], None],
    *,
    queue_group: str = "",
    stop_event: threading.Event | None = None,
    reconnect_delay_seconds: float = 2.0,
) -> None:
    """Blocking loop: subscribes to `subject` (in `queue_group` if given --
    NATS core load-balances a queue group's messages one-to-a-subscriber,
    the actual mechanism a horizontally-scaled worker pool needs, instead
    of fanning every message out to every subscriber the way plain SUB
    without a queue group would) and calls `handler(payload_bytes)` for
    each message, reconnecting with a fixed delay on any connection error.
    Runs until `stop_event` is set (a fresh, never-set Event if none is
    given -- i.e. runs forever) -- see app/consumer.py for the process
    that actually owns one and sets it on SIGTERM/SIGINT.
    """
    if not NATS_URL:
        raise RuntimeError("DRONE_NATS_URL is not set -- nothing to consume from")
    if stop_event is None:
        stop_event = threading.Event()

    while not stop_event.is_set():
        try:
            _subscribe_once(subject, queue_group, handler, stop_event)
        except (TimeoutError, OSError, ConnectionError) as exc:
            if stop_event.is_set():
                return
            logger.warning(
                "NATS consumer connection to subject '%s' failed (%s) -- reconnecting in %.0fs",
                subject, exc, reconnect_delay_seconds,
            )
            stop_event.wait(reconnect_delay_seconds)
