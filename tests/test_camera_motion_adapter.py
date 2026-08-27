from app.adapters.camera_motion import build_detection_payload, should_post


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
