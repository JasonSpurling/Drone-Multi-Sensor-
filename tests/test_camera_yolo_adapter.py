import argparse
import sys
import types
import urllib.error

from app.adapters.camera_yolo import build_detection_payload, classify_yolo_detection, main, watch


def test_non_aerial_class_is_skipped():
    assert classify_yolo_detection("car", 0.95) is None
    assert classify_yolo_detection("person", 0.99) is None
    assert classify_yolo_detection("dog", 0.8) is None


def test_bird_class_resolves_to_low_confidence():
    # A confident bird match should still land well below the drone
    # threshold, resolving to BIRD (not DRONE) via app/classification.py.
    confidence = classify_yolo_detection("bird", 0.95)
    assert confidence is not None
    assert confidence <= 0.2


def test_unrecognized_class_reports_scaled_model_confidence():
    confidence = classify_yolo_detection("kite", 0.8)
    assert confidence == 0.8 * 0.85


def test_unrecognized_class_confidence_is_capped():
    confidence = classify_yolo_detection("airplane", 1.0)
    assert confidence <= 0.95


def test_build_detection_payload_shape():
    payload = build_detection_payload(
        sensor_id="camera-yolo-1", target_lat=51.5, target_lon=-0.1,
        class_name="kite", model_confidence=0.7, box_xyxy=(10.0, 20.0, 30.0, 40.0),
    )
    assert payload["sensor_id"] == "camera-yolo-1"
    assert payload["sensor_type"] == "camera"
    assert payload["latitude"] == 51.5
    assert payload["longitude"] == -0.1
    assert payload["confidence"] == classify_yolo_detection("kite", 0.7)
    assert payload["raw_data"]["yolo_class"] == "kite"
    assert payload["raw_data"]["yolo_confidence"] == 0.7
    assert payload["raw_data"]["box_xyxy"] == [10.0, 20.0, 30.0, 40.0]
    assert "timestamp" in payload


def test_build_detection_payload_returns_none_for_non_aerial_class():
    payload = build_detection_payload(
        sensor_id="camera-yolo-1", target_lat=51.5, target_lon=-0.1,
        class_name="truck", model_confidence=0.9, box_xyxy=(0.0, 0.0, 1.0, 1.0),
    )
    assert payload is None


def test_snapshot_path_omitted_by_default():
    payload = build_detection_payload(
        sensor_id="camera-yolo-1", target_lat=51.5, target_lon=-0.1,
        class_name="kite", model_confidence=0.7, box_xyxy=(0.0, 0.0, 1.0, 1.0),
    )
    assert "snapshot_path" not in payload["raw_data"]


def test_snapshot_path_included_when_given():
    payload = build_detection_payload(
        sensor_id="camera-yolo-1", target_lat=51.5, target_lon=-0.1,
        class_name="kite", model_confidence=0.7, box_xyxy=(0.0, 0.0, 1.0, 1.0),
        snapshot_path="/tmp/camera-yolo-1-123.jpg",
    )
    assert payload["raw_data"]["snapshot_path"] == "/tmp/camera-yolo-1-123.jpg"


class _FakeCapture:
    def __init__(self, reads: list):
        self._reads = list(reads)
        self.released = False

    def isOpened(self):
        return True

    def read(self):
        return self._reads.pop(0) if self._reads else (False, None)

    def release(self):
        self.released = True


class _FakeBox:
    def __init__(self, class_idx: int, confidence: float, xyxy: tuple[float, float, float, float]):
        self.cls = [class_idx]
        self.conf = [confidence]
        self.xyxy = [xyxy]


class _FakeYoloResult:
    def __init__(self, names: dict, boxes: list):
        self.names = names
        self.boxes = boxes


def _fake_cv2_module(capture, imwrite_calls: list):
    fake = types.ModuleType("cv2")
    fake.VideoCapture = lambda source: capture
    fake.imwrite = lambda path, frame: imwrite_calls.append(path)
    return fake


def _fake_ultralytics_module(results, predict_calls: list):
    class _FakeYOLO:
        def __init__(self, model_path):
            self.model_path = model_path

        def predict(self, frame, conf, verbose):
            predict_calls.append((frame, conf))
            return results

    fake = types.ModuleType("ultralytics")
    fake.YOLO = _FakeYOLO
    return fake


def _yolo_args(**overrides):
    defaults = {
        "source": "0", "model": "yolov8n.pt", "sensor_id": "camera-yolo-1",
        "api_url": "http://127.0.0.1:8000/api/detections", "api_key": "", "max_retries": 0, "retry_backoff": 1.0,
        "target_lat": 51.5, "target_lon": -0.1, "min_model_confidence": 0.4, "min_interval": 0.0,
        "snapshot_dir": None,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def test_watch_posts_recognized_boxes_and_skips_non_aerial_ones(monkeypatch):
    names = {0: "kite", 1: "car"}
    boxes = [_FakeBox(0, 0.8, (1.0, 2.0, 3.0, 4.0)), _FakeBox(1, 0.9, (5.0, 6.0, 7.0, 8.0))]
    capture = _FakeCapture(reads=[(True, "frame1"), (False, None)])
    predict_calls: list = []
    monkeypatch.setitem(sys.modules, "cv2", _fake_cv2_module(capture, []))
    monkeypatch.setitem(
        sys.modules, "ultralytics", _fake_ultralytics_module([_FakeYoloResult(names, boxes)], predict_calls)
    )
    posted = []
    monkeypatch.setattr(
        "app.adapters.camera_yolo.post_detection",
        lambda url, payload, api_key, max_retries, retry_backoff_s: posted.append(payload) or {"track_id": 1},
    )

    watch(_yolo_args())

    assert len(predict_calls) == 1
    assert len(posted) == 1  # the "car" box was skipped (non-aerial), only "kite" posted
    assert posted[0]["raw_data"]["yolo_class"] == "kite"
    assert capture.released is True


def test_watch_writes_a_snapshot_when_a_snapshot_dir_is_configured(monkeypatch, tmp_path):
    names = {0: "kite"}
    boxes = [_FakeBox(0, 0.8, (1.0, 2.0, 3.0, 4.0))]
    capture = _FakeCapture(reads=[(True, "frame1"), (False, None)])
    imwrite_calls: list = []
    monkeypatch.setitem(sys.modules, "cv2", _fake_cv2_module(capture, imwrite_calls))
    monkeypatch.setitem(sys.modules, "ultralytics", _fake_ultralytics_module([_FakeYoloResult(names, boxes)], []))
    posted = []
    monkeypatch.setattr(
        "app.adapters.camera_yolo.post_detection",
        lambda url, payload, api_key, max_retries, retry_backoff_s: posted.append(payload) or {"track_id": 1},
    )

    watch(_yolo_args(snapshot_dir=str(tmp_path)))

    assert len(imwrite_calls) == 1
    assert posted[0]["raw_data"]["snapshot_path"] == imwrite_calls[0]


def test_watch_raises_systemexit_when_source_cannot_be_opened(monkeypatch):
    import pytest

    class _UnopenedCapture(_FakeCapture):
        def isOpened(self):
            return False

    monkeypatch.setitem(sys.modules, "cv2", _fake_cv2_module(_UnopenedCapture(reads=[]), []))
    monkeypatch.setitem(sys.modules, "ultralytics", _fake_ultralytics_module([], []))

    with pytest.raises(SystemExit):
        watch(_yolo_args())


def test_watch_skips_prediction_for_frames_within_min_interval(monkeypatch):
    # last_post only advances once a frame actually yields a recognized
    # (non-skipped) box -- frame1 posts a "kite" at t=2000.0, so frame2 at
    # t=2000.5 (well within the configured 1000s min_interval) should skip
    # prediction entirely rather than run it again needlessly.
    names = {0: "kite"}
    boxes = [_FakeBox(0, 0.8, (1.0, 2.0, 3.0, 4.0))]
    capture = _FakeCapture(reads=[(True, "frame1"), (True, "frame2"), (False, None)])
    predict_calls: list = []
    monkeypatch.setitem(sys.modules, "cv2", _fake_cv2_module(capture, []))
    monkeypatch.setitem(
        sys.modules, "ultralytics", _fake_ultralytics_module([_FakeYoloResult(names, boxes)], predict_calls)
    )
    monkeypatch.setattr(
        "app.adapters.camera_yolo.post_detection",
        lambda url, payload, api_key, max_retries, retry_backoff_s: {"track_id": 1},
    )
    clock = iter([2000.0, 2000.5])
    monkeypatch.setattr("app.adapters.camera_yolo.time.monotonic", lambda: next(clock))

    watch(_yolo_args(min_interval=1000.0))

    assert len(predict_calls) == 1


def test_watch_survives_a_post_failure(monkeypatch, capsys):
    names = {0: "kite"}
    boxes = [_FakeBox(0, 0.8, (1.0, 2.0, 3.0, 4.0))]
    capture = _FakeCapture(reads=[(True, "frame1"), (False, None)])
    monkeypatch.setitem(sys.modules, "cv2", _fake_cv2_module(capture, []))
    monkeypatch.setitem(sys.modules, "ultralytics", _fake_ultralytics_module([_FakeYoloResult(names, boxes)], []))

    def failing_post(url, payload, api_key, max_retries, retry_backoff_s):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr("app.adapters.camera_yolo.post_detection", failing_post)

    watch(_yolo_args())

    assert "ERROR posting detection" in capsys.readouterr().out
    assert capture.released is True


def test_main_parses_args_and_invokes_watch(monkeypatch):
    captured_args = {}
    monkeypatch.setattr("app.adapters.camera_yolo.watch", lambda args: captured_args.update(vars(args)))
    monkeypatch.setattr(
        "sys.argv",
        [
            "camera_yolo", "--source", "1", "--model", "my_model.pt", "--target-lat", "51.5",
            "--target-lon", "-0.1", "--min-model-confidence", "0.6", "--min-interval", "2",
            "--snapshot-dir", "/tmp/snaps",
        ],
    )

    main()

    assert captured_args["source"] == "1"
    assert captured_args["model"] == "my_model.pt"
    assert captured_args["target_lat"] == 51.5
    assert captured_args["min_model_confidence"] == 0.6
    assert captured_args["snapshot_dir"] == "/tmp/snaps"


def test_main_uses_documented_defaults(monkeypatch):
    captured_args = {}
    monkeypatch.setattr("app.adapters.camera_yolo.watch", lambda args: captured_args.update(vars(args)))
    monkeypatch.setattr("sys.argv", ["camera_yolo", "--target-lat", "51.5", "--target-lon", "-0.1"])

    main()

    assert captured_args["model"] == "yolov8n.pt"
    assert captured_args["snapshot_dir"] is None
    assert captured_args["sensor_id"] == "camera-yolo-1"
