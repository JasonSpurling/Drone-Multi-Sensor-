"""Verifies app/acoustic_beamforming.py against synthetic signals with a
known true bearing, computed independently of the module under test: a
point source is placed at an exact compass azimuth and each microphone's
propagation delay is derived from real Euclidean geometry (distance /
speed of sound), not by calling steering_delay_s to generate its own
"ground truth" -- that would be circular and could never catch a sign
error in the module itself (which an earlier draft of this module
actually had -- see its docstring).
"""

import numpy as np
import pytest

from app.acoustic_beamforming import SPEED_OF_SOUND_MPS, estimate_bearing, steering_delay_s

# A compact, ReSpeaker-scale square array (~6cm across).
SMALL_ARRAY = [(0.032, 0.032), (0.032, -0.032), (-0.032, -0.032), (-0.032, 0.032)]
# A larger, purpose-built ground-sensor-scale array (~30cm across).
LARGE_ARRAY = [(0.15, 0.15), (0.15, -0.15), (-0.15, -0.15), (-0.15, 0.15)]

SAMPLE_RATE_HZ = 48000.0


def _band_limited_noise(rng: np.random.Generator, n_samples: int, window: int = 60) -> np.ndarray:
    """A crude low-pass (moving-average) noise signal standing in for a
    real rotor/propeller acoustic signature, which is low-frequency
    dominated -- not scipy-dependent, just enough band-limiting to avoid
    the spatial-aliasing artifacts a full-bandwidth white-noise source
    causes with larger mic spacings.
    """
    raw = rng.standard_normal(n_samples + window - 1)
    kernel = np.ones(window) / window
    return np.convolve(raw, kernel, mode="valid")


def _synthesize(mic_positions, true_azimuth_deg: float, source_signal: np.ndarray, source_range_m: float = 1000.0):
    """Independent ground-truth synthesis: places an actual point source
    at true_azimuth_deg/source_range_m and computes each mic's exact
    Euclidean distance to it directly -- no reuse of steering_delay_s or
    any other function from the module under test.
    """
    azimuth_rad = np.radians(true_azimuth_deg)
    source_pos = np.array([np.sin(azimuth_rad), np.cos(azimuth_rad)]) * source_range_m
    delays_s = [
        np.hypot(source_pos[0] - mx, source_pos[1] - my) / SPEED_OF_SOUND_MPS
        for mx, my in mic_positions
    ]
    min_delay = min(delays_s)
    shifts = [round((delay - min_delay) * SAMPLE_RATE_HZ) for delay in delays_s]
    return np.array([np.roll(source_signal, shift) for shift in shifts])


def _angular_error(estimated_deg: float, true_deg: float) -> float:
    diff = abs(estimated_deg - true_deg) % 360
    return min(diff, 360 - diff)


@pytest.mark.parametrize("true_azimuth", [0, 45, 90, 135, 180, 225, 270, 315])
def test_small_array_recovers_bearing_within_20_degrees(true_azimuth):
    rng = np.random.default_rng(true_azimuth)  # deterministic per-case, still varied across cases
    source = _band_limited_noise(rng, 8192)
    channels = _synthesize(SMALL_ARRAY, true_azimuth, source)

    estimated, confidence = estimate_bearing(channels, SMALL_ARRAY, SAMPLE_RATE_HZ, azimuth_resolution_deg=1.0)

    assert _angular_error(estimated, true_azimuth) < 20.0
    assert confidence > 1.0  # a real peak, not a flat/undirected response


@pytest.mark.parametrize("true_azimuth", [0, 45, 90, 135, 180, 225, 270, 315])
def test_larger_array_is_more_accurate_than_the_small_one(true_azimuth):
    rng = np.random.default_rng(true_azimuth)
    source = _band_limited_noise(rng, 8192)
    channels = _synthesize(LARGE_ARRAY, true_azimuth, source)

    estimated, _confidence = estimate_bearing(channels, LARGE_ARRAY, SAMPLE_RATE_HZ, azimuth_resolution_deg=1.0)

    assert _angular_error(estimated, true_azimuth) < 6.0


def test_a_mic_closer_to_the_source_has_negative_delay_not_positive():
    # Regression test for the exact 180-degree sign bug this module had
    # during development: a mic sitting in the direction of the source
    # must be predicted to hear it *before* the array center (negative
    # delay), not after.
    mic_toward_source = (0.1, 0.0)  # due east of center
    delay = steering_delay_s(mic_toward_source, azimuth_deg=90.0)  # source due east
    assert delay < 0

    mic_away_from_source = (-0.1, 0.0)  # due west of center, away from an east source
    delay_away = steering_delay_s(mic_away_from_source, azimuth_deg=90.0)
    assert delay_away > 0


def test_uniform_signal_across_all_mics_gives_low_confidence():
    # No real directionality at all (e.g. diffuse/ambient noise, or a
    # source so far off-axis relative to the array's resolvable range that
    # every mic effectively hears the same thing) -- confidence should
    # reflect that rather than reporting a falsely sharp peak.
    rng = np.random.default_rng(0)
    identical = _band_limited_noise(rng, 4096)
    channels = np.array([identical] * len(SMALL_ARRAY))
    _, confidence = estimate_bearing(channels, SMALL_ARRAY, SAMPLE_RATE_HZ)
    assert confidence == pytest.approx(1.0, abs=0.05)


def test_rejects_mismatched_channel_and_position_counts():
    channels = np.zeros((3, 100))
    with pytest.raises(ValueError):
        estimate_bearing(channels, SMALL_ARRAY, SAMPLE_RATE_HZ)  # 4 positions, 3 channels


def test_rejects_fewer_than_two_microphones():
    channels = np.zeros((1, 100))
    with pytest.raises(ValueError):
        estimate_bearing(channels, [(0.0, 0.0)], SAMPLE_RATE_HZ)


def test_rejects_non_2d_channel_array():
    with pytest.raises(ValueError):
        estimate_bearing(np.zeros(100), SMALL_ARRAY, SAMPLE_RATE_HZ)
