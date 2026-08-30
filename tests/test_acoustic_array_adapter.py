import io
import urllib.error

import pytest

from app.adapters.acoustic_array_bridge import build_detection_payload, format_post_error, parse_mic_positions


def test_payload_reports_azimuth_and_range_not_latlon():
    payload = build_detection_payload(
        azimuth_deg=90.0, bearing_confidence=3.2, sensor_id="acoustic-1",
        assumed_range_m=150.0, confidence=0.6,
    )
    assert payload["azimuth_deg"] == 90.0
    assert payload["range_m"] == 150.0
    assert "latitude" not in payload
    assert "longitude" not in payload


def test_sensor_type_is_acoustic():
    payload = build_detection_payload(
        azimuth_deg=0.0, bearing_confidence=1.0, sensor_id="acoustic-1",
        assumed_range_m=100.0, confidence=0.5,
    )
    assert payload["sensor_type"] == "acoustic"


def test_classification_confidence_is_separate_from_bearing_confidence():
    # The two numbers mean different things -- "is this a drone" vs "how
    # sharp is the direction estimate" -- and must not be conflated.
    payload = build_detection_payload(
        azimuth_deg=180.0, bearing_confidence=8.5, sensor_id="acoustic-1",
        assumed_range_m=200.0, confidence=0.4,
    )
    assert payload["confidence"] == 0.4
    assert payload["raw_data"]["bearing_confidence"] == 8.5


def test_range_is_flagged_as_assumed_not_measured():
    payload = build_detection_payload(
        azimuth_deg=0.0, bearing_confidence=1.0, sensor_id="acoustic-1",
        assumed_range_m=100.0, confidence=0.5,
    )
    assert payload["raw_data"]["range_is_assumed_not_measured"] is True


def test_parse_mic_positions_accepts_a_valid_array():
    positions = parse_mic_positions("[[0.032,0.032],[0.032,-0.032],[-0.032,-0.032],[-0.032,0.032]]")
    assert positions == [[0.032, 0.032], [0.032, -0.032], [-0.032, -0.032], [-0.032, 0.032]]


def test_parse_mic_positions_rejects_fewer_than_two_mics():
    # app.acoustic_beamforming.estimate_bearing itself requires >= 2 mics --
    # this should fail immediately (before opening the audio device and
    # recording a block), not deep inside that module's own ValueError.
    with pytest.raises(SystemExit, match="at least 2 microphones"):
        parse_mic_positions("[[0.0, 0.0]]")


def test_format_post_error_includes_the_response_body_for_an_http_error():
    # Plain str(exc) on an HTTPError drops the JSON body FastAPI actually
    # sends (e.g. the reason an API key was rejected) -- that body is
    # exactly what an operator needs to see to fix the problem.
    exc = urllib.error.HTTPError(
        url="http://x/api/detections", code=401, msg="Unauthorized",
        hdrs=None, fp=io.BytesIO(b'{"detail": "Missing X-API-Key header"}'),
    )
    message = format_post_error(exc)
    assert "401" in message
    assert "Missing X-API-Key header" in message


def test_format_post_error_falls_back_to_str_for_a_non_http_error():
    exc = urllib.error.URLError("Connection refused")
    assert format_post_error(exc) == str(exc)
