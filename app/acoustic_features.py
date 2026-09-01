"""Mel-Frequency Cepstral Coefficients (MFCC) -- the standard spectral
feature used across acoustic classification (speech, and just as
commonly, rotor/propeller acoustic-signature detection) -- extracted from
a raw audio block via textbook DSP (framing + Hamming window -> power
spectrum -> triangular mel filterbank -> log -> DCT-II), not an external
library. This is well-established, unambiguous signal processing (the
same "textbook, no external reference needed" reasoning
app/acoustic_beamforming.py's own docstring gives for delay-and-sum
beamforming), unlike the reverse-engineered protocol byte layouts
elsewhere in this app's RF adapters that genuinely needed an external
source to get right.

Used by app/ml/train_acoustic.py (training) and
app/ml/acoustic_model.py (inference) -- the same "shared feature
extraction rules out train/serve skew" reasoning as app/ml/features.py.
Pure numpy, no new dependency beyond what app/acoustic_beamforming.py
already needs.
"""

from __future__ import annotations

import numpy as np

# Standard values for this: ~25ms frames capture a rotor blade's
# fundamental + several harmonics without smearing them together, ~10ms
# hop gives frames enough overlap that a transient isn't missed between
# them. 0.97 pre-emphasis is the conventional coefficient for boosting
# high-frequency content a first-order low-pass in the recording chain
# (and the physics of sound propagation itself) tends to attenuate.
DEFAULT_FRAME_SIZE_S = 0.025
DEFAULT_HOP_SIZE_S = 0.010
DEFAULT_PRE_EMPHASIS = 0.97


def _mel_filterbank(n_mels: int, n_fft: int, sample_rate_hz: float) -> np.ndarray:
    """A (n_mels, n_fft // 2 + 1) matrix of overlapping triangular
    filters, evenly spaced on the mel scale (which compresses high
    frequencies the way human -- and, close enough for this purpose,
    microphone-array -- hearing perceives them) rather than linearly on
    the raw FFT bins.
    """

    def hz_to_mel(hz: np.ndarray) -> np.ndarray:
        return 2595.0 * np.log10(1.0 + hz / 700.0)

    def mel_to_hz(mel: np.ndarray) -> np.ndarray:
        return 700.0 * (10.0 ** (mel / 2595.0) - 1.0)

    nyquist_hz = sample_rate_hz / 2.0
    mel_points = np.linspace(hz_to_mel(np.array(0.0)), hz_to_mel(np.array(nyquist_hz)), n_mels + 2)
    hz_points = mel_to_hz(mel_points)
    bin_indices = np.floor((n_fft + 1) * hz_points / sample_rate_hz).astype(int)
    bin_indices = np.clip(bin_indices, 0, n_fft // 2)

    filterbank = np.zeros((n_mels, n_fft // 2 + 1))
    for m in range(1, n_mels + 1):
        left, center, right = bin_indices[m - 1], bin_indices[m], bin_indices[m + 1]
        if center > left:
            filterbank[m - 1, left:center] = (np.arange(left, center) - left) / (center - left)
        if right > center:
            filterbank[m - 1, center:right] = (right - np.arange(center, right)) / (right - center)
    return filterbank


def _dct_ii_matrix(n_out: int, n_in: int) -> np.ndarray:
    """Orthonormal DCT-II basis, (n_out, n_in) -- applying this to a
    vector of log mel energies is the last MFCC step (decorrelating them,
    the way a Fourier transform decorrelates a time-domain signal),
    without pulling in scipy.fftpack for just this.
    """
    n = np.arange(n_in)
    k = np.arange(n_out).reshape(-1, 1)
    basis = np.cos(np.pi / n_in * (n + 0.5) * k)
    basis[0, :] *= np.sqrt(1.0 / n_in)
    basis[1:, :] *= np.sqrt(2.0 / n_in)
    return basis


def extract_mfcc(
    samples: np.ndarray,
    sample_rate_hz: float,
    n_mfcc: int = 13,
    n_mels: int = 26,
    frame_size_s: float = DEFAULT_FRAME_SIZE_S,
    hop_size_s: float = DEFAULT_HOP_SIZE_S,
    pre_emphasis: float = DEFAULT_PRE_EMPHASIS,
) -> np.ndarray:
    """`samples` is a single-channel 1D array (see
    app/adapters/acoustic_array_bridge.py for how a multi-mic array's
    channels are collapsed to one before calling this -- MFCC extraction
    doesn't need the array's spatial information, just its combined
    spectral content). Returns a (n_frames, n_mfcc) matrix, one MFCC
    vector per analysis frame; an audio block too short for even one full
    frame returns a (0, n_mfcc) array rather than raising, so a caller
    (see summarize_mfcc) can fall back to a defined-but-empty result
    instead of crashing detection ingest over one short recording.
    """
    if samples.ndim != 1:
        raise ValueError("samples must be a 1D array (already collapsed to a single channel)")

    emphasized = np.append(samples[0], samples[1:] - pre_emphasis * samples[:-1])

    frame_len = max(1, round(frame_size_s * sample_rate_hz))
    hop_len = max(1, round(hop_size_s * sample_rate_hz))
    n_frames = 1 + (len(emphasized) - frame_len) // hop_len if len(emphasized) >= frame_len else 0
    if n_frames <= 0:
        return np.zeros((0, n_mfcc))

    window = np.hamming(frame_len)
    n_fft = frame_len
    filterbank = _mel_filterbank(n_mels, n_fft, sample_rate_hz)
    dct_matrix = _dct_ii_matrix(n_mfcc, n_mels)

    mfcc = np.zeros((n_frames, n_mfcc))
    for i in range(n_frames):
        start = i * hop_len
        frame = emphasized[start : start + frame_len] * window
        power_spectrum = (np.abs(np.fft.rfft(frame, n=n_fft)) ** 2) / n_fft
        mel_energies = filterbank @ power_spectrum
        # A floor before log, not raw log(mel_energies): a filter that
        # landed entirely on a silent/near-zero-energy bin (routine near
        # the very top of a filterbank for typical audio) would otherwise
        # produce -inf, poisoning every downstream mean/std summary.
        log_mel_energies = np.log(np.maximum(mel_energies, 1e-10))
        mfcc[i] = dct_matrix @ log_mel_energies
    return mfcc


def summarize_mfcc(mfcc: np.ndarray) -> dict[str, float]:
    """Collapses a variable-length (n_frames, n_mfcc) matrix into a fixed-
    size flat dict (mean and standard deviation per coefficient, ready
    for sklearn's DictVectorizer) -- a classifier needs one fixed-size
    feature vector per audio block, not a variable number of per-frame
    ones. An empty `mfcc` (see extract_mfcc's short-audio case) returns
    zeros for every key rather than raising, so a caller can always
    build a payload even from a too-short recording.
    """
    n_mfcc = mfcc.shape[1] if mfcc.ndim == 2 and mfcc.shape[0] > 0 else 0
    if n_mfcc == 0:
        return {}
    means = mfcc.mean(axis=0)
    stds = mfcc.std(axis=0)
    features: dict[str, float] = {}
    for i in range(mfcc.shape[1]):
        features[f"acoustic_mfcc_mean_{i}"] = float(means[i])
        features[f"acoustic_mfcc_std_{i}"] = float(stds[i])
    return features
