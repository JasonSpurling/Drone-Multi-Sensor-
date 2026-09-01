import numpy as np
import pytest

from app.acoustic_features import extract_mfcc, summarize_mfcc

SAMPLE_RATE_HZ = 8000.0


def _tone(frequency_hz: float, duration_s: float = 0.5, sample_rate_hz: float = SAMPLE_RATE_HZ) -> np.ndarray:
    t = np.arange(0, duration_s, 1.0 / sample_rate_hz)
    return np.sin(2 * np.pi * frequency_hz * t)


def test_extract_mfcc_shape_matches_n_mfcc_and_frame_count():
    mfcc = extract_mfcc(_tone(150.0), SAMPLE_RATE_HZ, n_mfcc=13)
    assert mfcc.shape[1] == 13
    assert mfcc.shape[0] > 0


def test_extract_mfcc_is_deterministic():
    tone = _tone(150.0)
    first = extract_mfcc(tone, SAMPLE_RATE_HZ)
    second = extract_mfcc(tone, SAMPLE_RATE_HZ)
    assert np.array_equal(first, second)


def test_extract_mfcc_never_produces_nan_or_inf():
    for signal in [_tone(150.0), _tone(2000.0), np.zeros(4000)]:
        mfcc = extract_mfcc(signal, SAMPLE_RATE_HZ)
        assert not np.isnan(mfcc).any()
        assert not np.isinf(mfcc).any()


def test_extract_mfcc_distinguishes_different_frequencies():
    # Not asserting exact coefficient values (MFCCs are a decorrelated,
    # not directly interpretable, representation) -- just that a
    # low-frequency tone and a high-frequency tone produce measurably
    # different feature vectors, i.e. this is actually sensitive to
    # spectral content and not just returning a constant.
    low = extract_mfcc(_tone(150.0), SAMPLE_RATE_HZ)
    high = extract_mfcc(_tone(2000.0), SAMPLE_RATE_HZ)
    assert not np.allclose(low, high)


def test_extract_mfcc_on_silence_has_near_zero_variance_across_frames():
    silent = extract_mfcc(np.zeros(4000), SAMPLE_RATE_HZ)
    assert silent.std(axis=0).max() < 1e-6


def test_extract_mfcc_too_short_for_one_frame_returns_empty_not_a_crash():
    mfcc = extract_mfcc(np.zeros(5), SAMPLE_RATE_HZ, n_mfcc=13)
    assert mfcc.shape == (0, 13)


def test_extract_mfcc_rejects_a_2d_array():
    with pytest.raises(ValueError, match="1D"):
        extract_mfcc(np.zeros((2, 100)), SAMPLE_RATE_HZ)


def test_summarize_mfcc_returns_mean_and_std_per_coefficient():
    mfcc = extract_mfcc(_tone(150.0), SAMPLE_RATE_HZ, n_mfcc=5)
    summary = summarize_mfcc(mfcc)
    assert len(summary) == 10  # 5 coefficients * (mean + std)
    assert "acoustic_mfcc_mean_0" in summary
    assert "acoustic_mfcc_std_4" in summary


def test_summarize_mfcc_matches_numpy_mean_and_std():
    mfcc = extract_mfcc(_tone(150.0), SAMPLE_RATE_HZ, n_mfcc=5)
    summary = summarize_mfcc(mfcc)
    assert summary["acoustic_mfcc_mean_0"] == pytest.approx(float(mfcc[:, 0].mean()))
    assert summary["acoustic_mfcc_std_0"] == pytest.approx(float(mfcc[:, 0].std()))


def test_summarize_mfcc_of_empty_input_is_an_empty_dict():
    assert summarize_mfcc(np.zeros((0, 13))) == {}
