"""In-process pub/sub for pushing live updates (new/updated tracks,
opened incidents) to WebSocket-connected dashboard clients over
GET /ws/live (app/api/live.py), so a viewer sees something new in
milliseconds instead of waiting for its next poll.

Detection ingest (app/api/detections.py) runs as a sync FastAPI endpoint,
which FastAPI dispatches to a worker thread, not the asyncio event loop
the WebSocket connections live on -- same for the periodic behavior sweep
(app/main.py's _behavior_sweep_loop, run via asyncio.to_thread). Calling
asyncio.Queue.put_nowait() directly from either of those threads would
not be safe (asyncio.Queue is only safe from the loop's own thread), so
publish() hands off via loop.call_soon_threadsafe() instead.

Single-process scope: an event published on one replica only reaches
WebSocket clients connected to that same replica. Behind a load balancer
fronting multiple replicas, a client connected to a different replica
than the one that processed a given detection won't get that specific
push -- it still gets the data via its own polling fallback (see
dashboard.html), just not at push latency for that one event. Closing
that gap for real multi-replica push would mean routing this through
NATS (already optional infrastructure -- see app/queue_publisher.py);
out of scope here, and not a correctness issue, only a latency one.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging

logger = logging.getLogger(__name__)

_loop: asyncio.AbstractEventLoop | None = None
# site_id -> connected queues for that site. A WebSocket client only
# receives events for its own authenticated site (see app/api/live.py),
# same isolation guarantee as every other endpoint in this app.
_subscribers: dict[int, set[asyncio.Queue]] = {}

# Bounds how far a slow/stalled client can fall behind before this starts
# dropping its oldest queued event rather than growing unboundedly or
# blocking the publisher -- a dropped push is at worst a few seconds
# stale until the dashboard's own polling fallback re-fetches full state
# anyway, never lost data.
_MAX_QUEUED_PER_CLIENT = 100


def set_event_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Called once from app.main's lifespan startup."""
    global _loop
    _loop = loop


def subscribe(site_id: int) -> asyncio.Queue:
    queue: asyncio.Queue = asyncio.Queue(maxsize=_MAX_QUEUED_PER_CLIENT)
    _subscribers.setdefault(site_id, set()).add(queue)
    return queue


def unsubscribe(site_id: int, queue: asyncio.Queue) -> None:
    subs = _subscribers.get(site_id)
    if subs is None:
        return
    subs.discard(queue)
    if not subs:
        _subscribers.pop(site_id, None)


def publish(site_id: int, event: dict) -> None:
    """Best-effort, thread-safe, and never raises into the caller --
    detection ingest and the behavior sweep must succeed regardless of
    whether anyone is listening on a WebSocket right now, the same
    resilience contract app/cot_publisher.py's publish_track_cot() and
    app/queue_publisher.py's NATS publishing already hold to.
    """
    if _loop is None:
        return
    queues = _subscribers.get(site_id)
    if not queues:
        return
    payload = json.dumps(event)
    for queue in list(queues):
        try:
            _loop.call_soon_threadsafe(_put_dropping_oldest, queue, payload)
        except RuntimeError as exc:  # event loop already closed (shutdown)
            logger.warning("Live-update publish failed: %s", exc)


def _put_dropping_oldest(queue: asyncio.Queue, payload: str) -> None:
    if queue.full():
        with contextlib.suppress(asyncio.QueueEmpty):
            queue.get_nowait()
    queue.put_nowait(payload)


def reset_for_tests() -> None:
    global _loop
    _loop = None
    _subscribers.clear()
