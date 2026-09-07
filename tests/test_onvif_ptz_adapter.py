import argparse
import asyncio
import contextlib
import json
import sys
import types
import urllib.error

import pytest

from app.adapters.onvif_ptz_bridge import fetch_cue, normalize_to_range, watch


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


class _StopLoop(Exception):
    """Raised from a mocked asyncio.sleep to escape watch()'s `while True`
    after exactly one iteration, once that iteration's effects have
    already been asserted -- real onvif hardware/library isn't available
    in this environment (requirements-ptz.txt is an optional extra).
    """


def _fake_onvif_module(*, absolute_move_calls: list, pan_range=(-1.0, 1.0), tilt_range=(-1.0, 1.0)):
    profile = types.SimpleNamespace(
        token="profile-1", PTZConfiguration=types.SimpleNamespace(token="ptzconfig-1")
    )
    configuration_options = types.SimpleNamespace(
        SpacesTypes=types.SimpleNamespace(
            AbsolutePanTiltPositionSpace=[
                types.SimpleNamespace(
                    XRange=types.SimpleNamespace(Min=pan_range[0], Max=pan_range[1]),
                    YRange=types.SimpleNamespace(Min=tilt_range[0], Max=tilt_range[1]),
                )
            ]
        )
    )

    class _FakeMediaService:
        async def GetProfiles(self):
            return [profile]

    class _FakePtzService:
        async def GetConfigurationOptions(self, params):
            return configuration_options

        def create_type(self, name):
            return types.SimpleNamespace(ProfileToken=None, Position=None)

        async def AbsoluteMove(self, move_request):
            absolute_move_calls.append(move_request)

    class _FakeONVIFCamera:
        def __init__(self, host, port, user, password):
            pass

        async def update_xaddrs(self):
            return None

        def create_media_service(self):
            return _FakeMediaService()

        def create_ptz_service(self):
            return _FakePtzService()

    fake_module = types.ModuleType("onvif")
    fake_module.ONVIFCamera = _FakeONVIFCamera
    return fake_module


def _ptz_args(**overrides):
    defaults = {
        "track_id": 1, "cueing_camera_sensor_id": "ptz-cam-1", "camera_host": "192.168.1.50", "camera_port": 80,
        "camera_user": "admin", "camera_password": "secret", "poll_interval": 1.0,
        "api_url": "http://127.0.0.1:8000", "api_key": "",
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def test_watch_slews_to_the_normalized_cue_when_one_is_available(monkeypatch):
    calls = []
    monkeypatch.setitem(sys.modules, "onvif", _fake_onvif_module(absolute_move_calls=calls))
    monkeypatch.setattr(
        "app.adapters.onvif_ptz_bridge.fetch_cue",
        lambda api_url, track_id, camera_sensor_id, api_key="": {
            "pan_relative_deg": 90.0, "tilt_deg": 45.0, "distance_m": 120.0,
        },
    )

    async def stop_after_one(_seconds):
        raise _StopLoop

    monkeypatch.setattr("app.adapters.onvif_ptz_bridge.asyncio.sleep", stop_after_one)

    with contextlib.suppress(_StopLoop):
        asyncio.run(watch(_ptz_args()))

    assert len(calls) == 1
    # pan 90deg over a -180..180 source mapped into a -1..1 camera range -> 0.5;
    # tilt 45deg over a -90..90 source mapped into -1..1 -> 0.5.
    assert calls[0].Position["PanTilt"]["x"] == pytest.approx(0.5)
    assert calls[0].Position["PanTilt"]["y"] == pytest.approx(0.5)
    assert calls[0].ProfileToken == "profile-1"


def test_watch_holds_position_without_moving_when_no_cue_is_available(monkeypatch, capsys):
    calls = []
    monkeypatch.setitem(sys.modules, "onvif", _fake_onvif_module(absolute_move_calls=calls))
    monkeypatch.setattr(
        "app.adapters.onvif_ptz_bridge.fetch_cue",
        lambda api_url, track_id, camera_sensor_id, api_key="": None,
    )

    async def stop_after_one(_seconds):
        raise _StopLoop

    monkeypatch.setattr("app.adapters.onvif_ptz_bridge.asyncio.sleep", stop_after_one)

    with contextlib.suppress(_StopLoop):
        asyncio.run(watch(_ptz_args()))

    assert calls == []
    assert "holding position" in capsys.readouterr().out


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
