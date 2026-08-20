from app.adapters.camera_yolo import build_detection_payload, classify_yolo_detection


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
