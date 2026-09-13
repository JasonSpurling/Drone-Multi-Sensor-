import argparse
import sys
import types
import urllib.error

import pytest

from app.adapters.camera_motion import build_detection_payload, main, should_post, watch


def test_build_detection_payload_shape():
    payload = build_detection_payload(
        sensor_id="camera-1", target_lat=51.5, target_lon=-0.1, confidence=0.6, motion_area_px=1200.0
    )
    assert payload["sensor_id"] == "camera-1"
    assert payload["sensor_type"] == "camera"
    assert payload["latitude"] == 51.5
    assert payload["longitude"] == -0.1
    assert payload["confidence"] == 0.6
    assert payload["raw_data"]["motion_area_px"] == 1200.0
    assert "timestamp" in payload


def test_should_post_requires_both_area_and_interval():
    assert should_post(motion_area_px=1000, min_area_px=500, elapsed_s=3.0, min_interval_s=2.0) is True
    assert should_post(motion_area_px=100, min_area_px=500, elapsed_s=3.0, min_interval_s=2.0) is False
    assert should_post(motion_area_px=1000, min_area_px=500, elapsed_s=0.5, min_interval_s=2.0) is False


def test_should_post_boundary_values_are_inclusive():
    assert should_post(motion_area_px=500, min_area_px=500, elapsed_s=2.0, min_interval_s=2.0) is True


class _FakeCapture:
    def __init__(self, reads: list, opened: bool = True):
        self._reads = list(reads)
        self._opened = opened
        self.released = False

    def isOpened(self):
        return self._opened

    def read(self):
        return self._reads.pop(0) if self._reads else (False, None)

    def release(self):
        self.released = True


def _fake_cv2(capture: _FakeCapture, contour_area: float = 0.0):
    """A stand-in for the `cv2` module good enough for watch()/
    _largest_contour_area() -- real OpenCV isn't installed in this
    environment (it's an optional extra, requirements-camera.txt), and
    doesn't need to be to exercise this adapter's own orchestration logic
    (should_post gating, error handling, capture cleanup).
    """
    fake = types.ModuleType("cv2")
    fake.VideoCapture = lambda source: capture
    fake.createBackgroundSubtractorMOG2 = lambda detectShadows=False: types.SimpleNamespace(
        apply=lambda frame: "mask"
    )
    fake.RETR_EXTERNAL = 0
    fake.CHAIN_APPROX_SIMPLE = 0
    fake.findContours = lambda mask, mode, method: ([object()] if contour_area else [], None)
    fake.contourArea = lambda contour: contour_area
    return fake


def _args(**overrides):
    defaults = {
        "source": "0", "sensor_id": "camera-1", "api_url": "http://127.0.0.1:8000/api/detections", "api_key": "",
        "max_retries": 0, "retry_backoff": 1.0, "target_lat": 51.5, "target_lon": -0.1, "confidence": 0.6,
        "min_area": 500.0, "min_interval": 2.0,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def test_watch_raises_systemexit_when_source_cannot_be_opened(monkeypatch):
    capture = _FakeCapture(reads=[], opened=False)
    monkeypatch.setitem(sys.modules, "cv2", _fake_cv2(capture))
    with pytest.raises(SystemExit):
        watch(_args())


def test_watch_posts_a_detection_once_motion_exceeds_the_threshold_then_stops(monkeypatch):
    capture = _FakeCapture(reads=[(True, "frame1"), (False, None)])
    monkeypatch.setitem(sys.modules, "cv2", _fake_cv2(capture, contour_area=1200.0))
    posted = []
    monkeypatch.setattr(
        "app.adapters.camera_motion.post_detection",
        lambda url, payload, api_key, max_retries, retry_backoff_s: posted.append(payload) or {"track_id": 1},
    )

    watch(_args(min_area=500.0, min_interval=0.0))

    assert len(posted) == 1
    assert posted[0]["raw_data"]["motion_area_px"] == 1200.0
    assert capture.released is True  # cleaned up via the finally block, even on a clean stream-ended exit


def test_watch_does_not_post_when_motion_is_below_the_area_threshold(monkeypatch):
    capture = _FakeCapture(reads=[(True, "frame1"), (False, None)])
    monkeypatch.setitem(sys.modules, "cv2", _fake_cv2(capture, contour_area=10.0))
    posted = []
    monkeypatch.setattr(
        "app.adapters.camera_motion.post_detection",
        lambda url, payload, api_key, max_retries, retry_backoff_s: posted.append(payload),
    )

    watch(_args(min_area=500.0, min_interval=0.0))

    assert posted == []


def test_watch_survives_a_post_failure_and_keeps_reading(monkeypatch, capsys):
    capture = _FakeCapture(reads=[(True, "frame1"), (True, "frame2"), (False, None)])
    monkeypatch.setitem(sys.modules, "cv2", _fake_cv2(capture, contour_area=1200.0))
    calls = []

    def failing_post(url, payload, api_key, max_retries, retry_backoff_s):
        calls.append(payload)
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr("app.adapters.camera_motion.post_detection", failing_post)

    watch(_args(min_area=500.0, min_interval=0.0))

    # Both motion frames were still processed despite the first POST failing.
    assert len(calls) == 2
    assert "ERROR posting detection" in capsys.readouterr().out
    assert capture.released is True


def test_watch_releases_capture_even_when_the_loop_is_interrupted(monkeypatch):
    # A raised exception mid-loop (not just a clean "stream ended") should
    # still hit the finally block -- capture.release() isn't optional
    # cleanup, it's a real camera/file handle.
    capture = _FakeCapture(reads=[(True, "frame1")])
    fake_cv2 = _fake_cv2(capture, contour_area=0.0)
    fake_cv2.findContours = lambda mask, mode, method: (_ for _ in ()).throw(RuntimeError("boom"))
    monkeypatch.setitem(sys.modules, "cv2", fake_cv2)

    with pytest.raises(RuntimeError):
        watch(_args())

    assert capture.released is True


def test_main_parses_args_and_invokes_watch(monkeypatch):
    captured_args = {}
    monkeypatch.setattr("app.adapters.camera_motion.watch", lambda args: captured_args.update(vars(args)))
    monkeypatch.setattr(
        "sys.argv",
        [
            "camera_motion", "--source", "rtsp://cam/x", "--target-lat", "51.5", "--target-lon", "-0.1",
            "--confidence", "0.7", "--min-area", "800", "--min-interval", "5",
        ],
    )

    main()

    assert captured_args["source"] == "rtsp://cam/x"
    assert captured_args["target_lat"] == 51.5
    assert captured_args["target_lon"] == -0.1
    assert captured_args["confidence"] == 0.7
    assert captured_args["min_area"] == 800.0
    assert captured_args["min_interval"] == 5.0


def test_main_uses_documented_defaults(monkeypatch):
    captured_args = {}
    monkeypatch.setattr("app.adapters.camera_motion.watch", lambda args: captured_args.update(vars(args)))
    monkeypatch.setattr("sys.argv", ["camera_motion", "--target-lat", "51.5", "--target-lon", "-0.1"])

    main()

    assert captured_args["source"] == "0"
    assert captured_args["sensor_id"] == "camera-1"
    assert captured_args["confidence"] == 0.5
