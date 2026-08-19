import json
import urllib.error

from app.adapters.onvif_ptz_bridge import fetch_cue, normalize_to_range


def test_normalize_maps_midpoint_correctly():
    assert normalize_to_range(0.0, -180.0, 180.0, -1.0, 1.0) == 0.0


def test_normalize_maps_endpoints_correctly():
    assert normalize_to_range(-180.0, -180.0, 180.0, -1.0, 1.0) == -1.0
    assert normalize_to_range(180.0, -180.0, 180.0, -1.0, 1.0) == 1.0


def test_normalize_clamps_out_of_range_values():
    assert normalize_to_range(200.0, -180.0, 180.0, -1.0, 1.0) == 1.0
    assert normalize_to_range(-200.0, -180.0, 180.0, -1.0, 1.0) == -1.0


def test_normalize_handles_degenerate_zero_width_source_range():
    # value_min == value_max shouldn't divide by zero.
    assert normalize_to_range(5.0, 5.0, 5.0, -1.0, 1.0) == -1.0


def test_normalize_supports_non_symmetric_target_ranges():
    # Some cameras report pan ranges like [0, 360] rather than [-1, 1].
    assert normalize_to_range(90.0, -180.0, 180.0, 0.0, 360.0) == 270.0


class _FakeResponse:
    def __init__(self, body: dict):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self._body).encode()


def test_fetch_cue_returns_parsed_json_on_success(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout=None: _FakeResponse({"pan_deg": 90.0, "tilt_deg": 5.0}),
    )
    cue = fetch_cue("http://127.0.0.1:8000", track_id=1, camera_sensor_id="cam-1")
    assert cue == {"pan_deg": 90.0, "tilt_deg": 5.0}


def test_fetch_cue_returns_none_on_404():
    def raise_404(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)

    import urllib.request as urllib_request_module
    orig = urllib_request_module.urlopen
    urllib_request_module.urlopen = raise_404
    try:
        assert fetch_cue("http://127.0.0.1:8000", track_id=1, camera_sensor_id="cam-1") is None
    finally:
        urllib_request_module.urlopen = orig


def test_fetch_cue_returns_none_on_409_track_has_no_position():
    def raise_409(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 409, "Conflict", {}, None)

    import urllib.request as urllib_request_module
    orig = urllib_request_module.urlopen
    urllib_request_module.urlopen = raise_409
    try:
        assert fetch_cue("http://127.0.0.1:8000", track_id=1, camera_sensor_id="cam-1") is None
    finally:
        urllib_request_module.urlopen = orig


def test_fetch_cue_reraises_other_http_errors():
    def raise_500(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 500, "Server Error", {}, None)

    import urllib.request as urllib_request_module
    import pytest

    orig = urllib_request_module.urlopen
    urllib_request_module.urlopen = raise_500
    try:
        with pytest.raises(urllib.error.HTTPError):
            fetch_cue("http://127.0.0.1:8000", track_id=1, camera_sensor_id="cam-1")
    finally:
        urllib_request_module.urlopen = orig
