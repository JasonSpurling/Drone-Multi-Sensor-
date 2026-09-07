import argparse
import contextlib
import sys
import types
import urllib.error

import numpy as np
import pytest

from app.adapters.acoustic_array_bridge import build_detection_payload, parse_mic_positions, watch


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
    assert positions == [(0.032, 0.032), (0.032, -0.032), (-0.032, -0.032), (-0.032, 0.032)]


def test_parse_mic_positions_rejects_fewer_than_two_mics():
    # app.acoustic_beamforming.estimate_bearing itself requires >= 2 mics --
    # this should fail immediately (before opening the audio device and
    # recording a block), not deep inside that module's own ValueError.
    with pytest.raises(SystemExit, match="at least 2 microphones"):
        parse_mic_positions("[[0.0, 0.0]]")


class _StopLoop(Exception):
    """Raised from a mocked sd.rec to escape watch()'s `while True` after
    exactly one block -- real sounddevice/audio hardware isn't available
    in this environment (requirements-acoustic.txt is an optional extra).
    """


def _fake_sounddevice_module(recordings: list):
    """Each queued array in `recordings` is returned by one sd.rec() call
    (shape (n_samples, n_channels), matching real sounddevice's own
    convention); once exhausted, the next call raises _StopLoop.
    """
    queue = list(recordings)

    def fake_rec(frames, samplerate, channels, device):
        if not queue:
            raise _StopLoop
        return queue.pop(0)

    fake = types.ModuleType("sounddevice")
    fake.rec = fake_rec
    fake.wait = lambda: None
    return fake


def _acoustic_args(**overrides):
    defaults = {
        "mic_positions": "[[0.032,0.032],[0.032,-0.032],[-0.032,-0.032],[-0.032,0.032]]",
        "sample_rate": 48000.0, "block_seconds": 0.01, "azimuth_resolution_deg": 10.0, "assumed_range_m": 150.0,
        "confidence": 0.6, "device": None, "sensor_id": "acoustic-1",
        "api_url": "http://127.0.0.1:8000/api/detections", "api_key": "", "max_retries": 0, "retry_backoff": 1.0,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _fake_block(n_mics: int, n_samples: int) -> np.ndarray:
    rng = np.random.default_rng(seed=0)
    return rng.standard_normal((n_samples, n_mics)).astype(np.float32)


def test_watch_posts_one_detection_per_recorded_block(monkeypatch):
    args = _acoustic_args()
    n_mics = 4
    n_samples = int(args.block_seconds * args.sample_rate)
    monkeypatch.setitem(sys.modules, "sounddevice", _fake_sounddevice_module([_fake_block(n_mics, n_samples)]))
    posted = []
    monkeypatch.setattr(
        "app.adapters.acoustic_array_bridge.post_detection",
        lambda url, payload, api_key, max_retries, retry_backoff_s: posted.append(payload) or {"track_id": 1},
    )

    with contextlib.suppress(_StopLoop):
        watch(args)

    assert len(posted) == 1
    assert posted[0]["sensor_type"] == "acoustic"
    assert posted[0]["range_m"] == 150.0
    # No ML model configured in this test env -- classify_audio() returns
    # None, so the manual --confidence estimate is what actually gets used.
    assert posted[0]["confidence"] == 0.6


def test_watch_survives_a_post_failure_and_keeps_recording(monkeypatch, capsys):
    args = _acoustic_args()
    n_mics = 4
    n_samples = int(args.block_seconds * args.sample_rate)
    monkeypatch.setitem(
        sys.modules, "sounddevice",
        _fake_sounddevice_module([_fake_block(n_mics, n_samples), _fake_block(n_mics, n_samples)]),
    )
    calls = []

    def failing_post(url, payload, api_key, max_retries, retry_backoff_s):
        calls.append(payload)
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr("app.adapters.acoustic_array_bridge.post_detection", failing_post)

    with contextlib.suppress(_StopLoop):
        watch(args)

    assert len(calls) == 2  # the first block's post failure didn't stop the second block from being recorded
    assert "ERROR posting detection" in capsys.readouterr().out


def test_watch_uses_ml_confidence_when_a_model_is_configured(monkeypatch):
    args = _acoustic_args()
    n_mics = 4
    n_samples = int(args.block_seconds * args.sample_rate)
    monkeypatch.setitem(sys.modules, "sounddevice", _fake_sounddevice_module([_fake_block(n_mics, n_samples)]))
    monkeypatch.setattr("app.adapters.acoustic_array_bridge.classify_audio", lambda samples, sample_rate: 0.92)
    posted = []
    monkeypatch.setattr(
        "app.adapters.acoustic_array_bridge.post_detection",
        lambda url, payload, api_key, max_retries, retry_backoff_s: posted.append(payload) or {"track_id": 1},
    )

    with contextlib.suppress(_StopLoop):
        watch(args)

    assert posted[0]["confidence"] == 0.92  # the ML model's opinion, not args.confidence (0.6)
