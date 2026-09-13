"""app.main's periodic background sweeps -- retention purging and the
formation/shadowing behavior sweep, both started as asyncio tasks in the
app's lifespan handler. Each `while True: await asyncio.sleep(...)` loop
body is tested here by mocking asyncio.sleep to raise a sentinel
exception after exactly one iteration, since these are real infinite
loops with no other natural exit point (the same technique this repo's
hardware-adapter bridge scripts already use for their own watch() loops).
"""

import asyncio
import contextlib

from app.main import _behavior_sweep_loop, _retention_sweep_loop, _run_behavior_sweep


class _StopLoop(Exception):
    pass


def test_run_behavior_sweep_checks_formation_and_shadowing_per_site(monkeypatch):
    site1 = type("Site", (), {"id": 1})()
    site2 = type("Site", (), {"id": 2})()
    tracks_by_site = {1: ["track-a", "track-b"], 2: ["track-c"]}
    formation_calls = []
    shadowing_calls = []

    monkeypatch.setattr("app.main.list_sites", lambda: [site1, site2])
    monkeypatch.setattr(
        "app.main.list_tracks", lambda site_id, status: tracks_by_site[site_id]
    )
    monkeypatch.setattr("app.main.check_formation_incidents", lambda tracks: formation_calls.append(tracks))
    monkeypatch.setattr("app.main.check_shadowing_incidents", lambda tracks: shadowing_calls.append(tracks))

    _run_behavior_sweep()

    # Each site's tracks are only ever compared against that same site's
    # other tracks -- never mixed across sites.
    assert formation_calls == [["track-a", "track-b"], ["track-c"]]
    assert shadowing_calls == [["track-a", "track-b"], ["track-c"]]


def _sleep_then_stop():
    """asyncio.sleep() is the very first thing each loop iteration awaits,
    before the iteration's actual work -- a mock that raises on the first
    call would escape before that work ever runs. This lets exactly one
    full iteration's body execute, then aborts the loop on the second
    sleep (i.e. once that iteration is done) -- a fresh counter per test,
    since monkeypatch.setattr needs a new callable each time.
    """
    calls = 0

    async def fake_sleep(_seconds):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise _StopLoop

    return fake_sleep


def test_retention_sweep_loop_purges_each_configured_retention_kind(monkeypatch):
    monkeypatch.setattr("app.main.DETECTION_RETENTION_DAYS", 30)
    monkeypatch.setattr("app.main.TRACK_RETENTION_DAYS", 90)
    monkeypatch.setattr("app.main.AUDIT_LOG_RETENTION_DAYS", 365)
    monkeypatch.setattr("app.main.asyncio.sleep", _sleep_then_stop())

    purged = {"detections": None, "tracks": None, "audit_log": None}
    monkeypatch.setattr(
        "app.main.purge_old_detections", lambda cutoff: purged.__setitem__("detections", cutoff) or 3
    )
    monkeypatch.setattr("app.main.purge_old_tracks", lambda cutoff: purged.__setitem__("tracks", cutoff) or 2)
    monkeypatch.setattr(
        "app.main.purge_old_audit_log", lambda cutoff: purged.__setitem__("audit_log", cutoff) or 1
    )

    with contextlib.suppress(_StopLoop):
        asyncio.run(_retention_sweep_loop())

    assert purged["detections"] is not None
    assert purged["tracks"] is not None
    assert purged["audit_log"] is not None


def test_retention_sweep_loop_skips_purge_kinds_with_retention_disabled(monkeypatch):
    # 0 (or negative) means "keep forever" for that kind -- its purge
    # function must never be called, not called with a meaningless cutoff.
    monkeypatch.setattr("app.main.DETECTION_RETENTION_DAYS", 30)
    monkeypatch.setattr("app.main.TRACK_RETENTION_DAYS", 0)
    monkeypatch.setattr("app.main.AUDIT_LOG_RETENTION_DAYS", 0)
    monkeypatch.setattr("app.main.asyncio.sleep", _sleep_then_stop())

    monkeypatch.setattr("app.main.purge_old_detections", lambda cutoff: 0)

    def fail_if_called(cutoff):
        raise AssertionError("should not be called when retention is disabled")

    monkeypatch.setattr("app.main.purge_old_tracks", fail_if_called)
    monkeypatch.setattr("app.main.purge_old_audit_log", fail_if_called)

    with contextlib.suppress(_StopLoop):
        asyncio.run(_retention_sweep_loop())


def test_retention_sweep_loop_returns_immediately_when_everything_is_disabled(monkeypatch):
    # No asyncio.sleep mock needed here -- if the loop is ever entered at
    # all with all three retention kinds disabled, this test would hang
    # (a real `while True` with no other exit), so a real event loop
    # actually returning proves the early-return guard works.
    monkeypatch.setattr("app.main.DETECTION_RETENTION_DAYS", 0)
    monkeypatch.setattr("app.main.TRACK_RETENTION_DAYS", 0)
    monkeypatch.setattr("app.main.AUDIT_LOG_RETENTION_DAYS", 0)

    asyncio.run(_retention_sweep_loop())  # must return on its own, not hang


def test_behavior_sweep_loop_runs_the_sweep_each_cycle(monkeypatch):
    monkeypatch.setattr("app.main.asyncio.sleep", _sleep_then_stop())
    calls = []
    monkeypatch.setattr("app.main._run_behavior_sweep", lambda: calls.append(1))

    with contextlib.suppress(_StopLoop):
        asyncio.run(_behavior_sweep_loop())

    assert calls == [1]


def test_behavior_sweep_loop_logs_and_survives_a_failed_sweep(monkeypatch, caplog):
    import logging

    monkeypatch.setattr("app.main.asyncio.sleep", _sleep_then_stop())

    def failing_sweep():
        raise RuntimeError("boom")

    monkeypatch.setattr("app.main._run_behavior_sweep", failing_sweep)

    with caplog.at_level(logging.ERROR), contextlib.suppress(_StopLoop):
        asyncio.run(_behavior_sweep_loop())

    assert any("Behavior sweep failed" in r.message for r in caplog.records)
