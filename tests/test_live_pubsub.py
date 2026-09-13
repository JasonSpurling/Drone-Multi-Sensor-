"""app.live's in-process pub/sub plumbing (subscribe/unsubscribe/publish/
_put_dropping_oldest) exercised directly -- tests/test_live_websocket.py
and tests/test_live_shutdown.py already cover the real end-to-end
WebSocket path; this covers the smaller defensive/edge branches that
don't need a real connected client (no loop set yet, no subscribers for a
site, a closed event loop, a slow client's queue overflowing).
"""

import asyncio
import json

import app.live as live


def test_unsubscribe_is_a_noop_for_a_site_with_no_subscribers():
    live.reset_for_tests()
    queue: asyncio.Queue = asyncio.Queue()
    live.unsubscribe(999, queue)  # must not raise


def test_close_all_is_a_noop_before_the_event_loop_is_ever_set():
    live.reset_for_tests()
    live.close_all()  # must not raise


def test_publish_is_a_noop_before_the_event_loop_is_ever_set():
    live.reset_for_tests()
    live.publish(1, {"type": "track_update"})  # must not raise


def test_publish_is_a_noop_with_no_subscribers_for_that_site():
    live.reset_for_tests()
    loop = asyncio.new_event_loop()
    try:
        live.set_event_loop(loop)
        live.publish(1, {"type": "track_update"})  # no one subscribed to site 1 -- must not raise
    finally:
        loop.close()
        live.reset_for_tests()


def test_publish_logs_and_survives_a_closed_event_loop(caplog):
    live.reset_for_tests()
    loop = asyncio.new_event_loop()
    queue = live.subscribe(1)
    loop.close()  # simulates shutdown racing a still-in-flight publish
    live.set_event_loop(loop)

    with caplog.at_level("WARNING"):
        live.publish(1, {"type": "track_update"})  # must not raise

    assert "Live-update publish failed" in caplog.text
    assert queue.empty()  # nothing was actually delivered
    live.reset_for_tests()


def test_put_dropping_oldest_drops_the_oldest_entry_once_full():
    queue: asyncio.Queue = asyncio.Queue(maxsize=2)
    live._put_dropping_oldest(queue, "first")
    live._put_dropping_oldest(queue, "second")
    assert queue.full()

    live._put_dropping_oldest(queue, "third")

    assert queue.qsize() == 2
    remaining = [queue.get_nowait(), queue.get_nowait()]
    assert remaining == ["second", "third"]  # "first" was dropped, not "third"


def test_publish_json_encodes_the_event_for_a_real_subscriber():
    live.reset_for_tests()
    loop = asyncio.new_event_loop()
    try:
        live.set_event_loop(loop)
        queue = live.subscribe(1)
        live.publish(1, {"type": "track_update", "track": {"id": 1}})
        loop.run_until_complete(asyncio.sleep(0))  # let the scheduled callback run
        payload = queue.get_nowait()
        assert json.loads(payload) == {"type": "track_update", "track": {"id": 1}}
    finally:
        loop.close()
        live.reset_for_tests()
