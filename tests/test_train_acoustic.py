"""app.ml.train_acoustic -- tested against small, trivially-separable
synthetic WAV recordings (obviously not real rotor/bird/aircraft audio;
see app/ml/__init__.py's own docstring for why this repo has none). These
files exist purely to prove the training *pipeline* actually works
end-to-end (WAV loading -> MFCC feature extraction -> a real scikit-learn
Pipeline.fit() -> a real held-out evaluation -> a real joblib-loadable
model file) -- not to claim the resulting model is fit for real
classification.
"""

import math
import random
import struct
import wave
from pathlib import Path

import pytest

from app.ml.train_acoustic import load_dataset, load_wav_mono, main, train

pytest.importorskip("sklearn")

SAMPLE_RATE_HZ = 8000


def _write_tone_wav(
    path: Path, frequency_hz: float, duration_s: float = 0.3, n_channels: int = 1, seed: int = 0
) -> None:
    # A little noise, not a pure noiseless tone -- a frequency landing
    # exactly on an FFT bin boundary vs. between two produces very
    # different spectral-leakage characteristics for an idealized pure
    # tone, which made an earlier version of this fixture flaky depending
    # on which exact frequencies happened to align with which bins. A
    # small amount of jitter (still trivially separable by pitch) avoids
    # that without needing to hand-pick bin-aligned frequencies.
    rng = random.Random(seed)
    n_samples = int(duration_s * SAMPLE_RATE_HZ)
    with wave.open(str(path), "wb") as f:
        f.setnchannels(n_channels)
        f.setsampwidth(2)
        f.setframerate(SAMPLE_RATE_HZ)
        frames = bytearray()
        for i in range(n_samples):
            value = math.sin(2 * math.pi * frequency_hz * i / SAMPLE_RATE_HZ)
            value += 0.05 * (rng.random() * 2 - 1)
            value = max(-1.0, min(1.0, value))
            sample = int(value * 30000)
            frames += struct.pack("<h", sample) * n_channels
        f.writeframes(bytes(frames))


def _build_dataset(root: Path, n_per_label: int = 10) -> None:
    # Two trivially-separable classes on pitch alone -- low frequency
    # always "drone" (rotor-like), high frequency always "bird" -- so a
    # trained classifier is verifiably able to learn *something* real
    # from the MFCC features, without this being genuine labeled audio.
    # Frequencies are randomized within each band (a fixed seed, so the
    # dataset itself is deterministic) rather than a fixed list: a pure
    # tone landing exactly on an FFT bin boundary (bin width here is
    # SAMPLE_RATE_HZ / frame_len = 40Hz) produces very different spectral-
    # leakage characteristics than one that doesn't, which made an earlier
    # version of this fixture flaky for the specific frequencies it
    # happened to hard-code. Randomizing avoids depending on any single
    # frequency's luck; enough samples per label makes the classifier
    # robust to the handful of individual files that do land badly.
    rng = random.Random(1234)
    labeled_ranges = [("drone", (100.0, 160.0)), ("bird", (2800.0, 3600.0))]
    for label, (low_hz, high_hz) in labeled_ranges:
        label_dir = root / label
        label_dir.mkdir()
        for i in range(n_per_label):
            freq = rng.uniform(low_hz, high_hz)
            _write_tone_wav(label_dir / f"{i}.wav", freq, seed=rng.randint(0, 1_000_000))


def test_load_wav_mono_reads_sample_rate_and_normalizes_to_unit_range(tmp_path):
    path = tmp_path / "tone.wav"
    _write_tone_wav(path, 150.0)
    samples, sample_rate_hz = load_wav_mono(path)
    assert sample_rate_hz == SAMPLE_RATE_HZ
    assert samples.max() <= 1.0
    assert samples.min() >= -1.0


def test_load_wav_mono_averages_multichannel_files(tmp_path):
    path = tmp_path / "stereo.wav"
    _write_tone_wav(path, 150.0, n_channels=2)
    samples, _ = load_wav_mono(path)
    assert samples.ndim == 1


def _write_8bit_tone_wav(path: Path, frequency_hz: float, duration_s: float = 0.3) -> None:
    n_samples = int(duration_s * SAMPLE_RATE_HZ)
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(1)  # 8-bit PCM is unsigned, centered on 128
        f.setframerate(SAMPLE_RATE_HZ)
        frames = bytearray()
        for i in range(n_samples):
            value = math.sin(2 * math.pi * frequency_hz * i / SAMPLE_RATE_HZ)
            frames.append(int(128 + value * 100))
        f.writeframes(bytes(frames))


def test_load_wav_mono_rejects_an_unsupported_sample_width(tmp_path):
    path = tmp_path / "tone24.wav"
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(3)  # 24-bit PCM -- not one of the supported 8/16/32-bit widths
        f.setframerate(SAMPLE_RATE_HZ)
        f.writeframes(b"\x00\x00\x00" * 100)
    with pytest.raises(SystemExit, match="unsupported WAV sample width"):
        load_wav_mono(path)


def test_load_wav_mono_centers_unsigned_8_bit_pcm_on_zero(tmp_path):
    path = tmp_path / "tone8.wav"
    _write_8bit_tone_wav(path, 150.0)
    samples, sample_rate_hz = load_wav_mono(path)
    assert sample_rate_hz == SAMPLE_RATE_HZ
    # 8-bit PCM is unsigned (0-255, centered on 128) -- if it weren't
    # re-centered before scaling, this would sit entirely above zero
    # instead of oscillating around it like every other sample width.
    assert samples.mean() == pytest.approx(0.0, abs=0.05)
    assert samples.min() < 0.0


def test_load_dataset_skips_a_recording_too_short_for_any_mfcc_frame(tmp_path, capsys):
    (tmp_path / "drone").mkdir()
    _write_tone_wav(tmp_path / "drone" / "real.wav", 150.0)
    # A handful of samples -- far shorter than a single MFCC analysis
    # frame, so extract_mfcc produces zero frames to summarize.
    _write_tone_wav(tmp_path / "drone" / "too_short.wav", 150.0, duration_s=0.001)

    features_list, labels = load_dataset(str(tmp_path))

    assert len(features_list) == 1  # only the real recording contributed features
    assert labels == ["drone"]
    output = capsys.readouterr().out
    assert "too_short.wav" in output
    assert "too short" in output


def test_load_dataset_rejects_an_unrecognized_label_directory(tmp_path):
    (tmp_path / "not_a_real_label").mkdir()
    _write_tone_wav(tmp_path / "not_a_real_label" / "0.wav", 150.0)
    with pytest.raises(SystemExit, match="not_a_real_label"):
        load_dataset(str(tmp_path))


def test_load_dataset_rejects_a_missing_directory():
    with pytest.raises(SystemExit, match="not a directory"):
        load_dataset("/no/such/directory")


def test_train_rejects_too_few_recordings(tmp_path):
    (tmp_path / "drone").mkdir()
    _write_tone_wav(tmp_path / "drone" / "0.wav", 150.0)
    with pytest.raises(SystemExit, match="Only 1"):
        train(str(tmp_path), str(tmp_path / "model.joblib"))


def test_train_rejects_a_label_with_too_few_recordings_to_split(tmp_path):
    (tmp_path / "drone").mkdir()
    for i in range(9):
        _write_tone_wav(tmp_path / "drone" / f"{i}.wav", 120 + i)
    (tmp_path / "bird").mkdir()
    _write_tone_wav(tmp_path / "bird" / "0.wav", 3000.0)
    with pytest.raises(SystemExit, match="bird"):
        train(str(tmp_path), str(tmp_path / "model.joblib"), test_size=0.3, random_state=0)


def test_train_produces_a_loadable_model_that_predicts_sensibly(tmp_path, capsys):
    _build_dataset(tmp_path)
    model_path = tmp_path / "model.joblib"

    train(str(tmp_path), str(model_path), test_size=0.3, random_state=0)

    assert model_path.is_file()
    output = capsys.readouterr().out
    assert "Saved model to" in output
    assert "precision" in output

    import joblib

    from app.acoustic_features import extract_mfcc, summarize_mfcc

    model = joblib.load(model_path)
    # Aggregate accuracy across every recording, not one hand-picked file
    # per label -- a pure-tone MFCC feature vector can land as an outlier
    # for any single file depending on how its frequency happens to align
    # with the FFT's bin grid (see _build_dataset's comment); asserting on
    # the whole set is what actually proves the classifier learned the
    # real pitch-based pattern, without being fragile to that per-file luck.
    correct = 0
    total = 0
    for label_dir in sorted(tmp_path.iterdir()):
        if not label_dir.is_dir():
            continue
        for wav_path in sorted(label_dir.glob("*.wav")):
            samples, sr = load_wav_mono(wav_path)
            predicted = model.predict([summarize_mfcc(extract_mfcc(samples, sr))])[0]
            correct += predicted == label_dir.name
            total += 1
    assert correct / total >= 0.9


def test_train_prints_cross_validation_accuracy(tmp_path, capsys):
    _build_dataset(tmp_path)
    model_path = tmp_path / "model.joblib"

    train(str(tmp_path), str(model_path), test_size=0.3, random_state=0)

    output = capsys.readouterr().out
    assert "5-fold cross-validation accuracy" in output


def test_train_prints_feature_importances(tmp_path, capsys):
    _build_dataset(tmp_path)
    model_path = tmp_path / "model.joblib"

    train(str(tmp_path), str(model_path), test_size=0.3, random_state=0)

    output = capsys.readouterr().out
    assert "Feature importances" in output
    assert "acoustic_mfcc" in output


def test_main_parses_args_and_invokes_train(tmp_path, monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "app.ml.train_acoustic.train",
        lambda data_dir, out_path, test_size, random_state: captured.update(
            data_dir=data_dir, out_path=out_path, test_size=test_size, random_state=random_state
        ),
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "train_acoustic", "--data-dir", str(tmp_path), "--out", str(tmp_path / "model.joblib"),
            "--test-size", "0.3", "--random-state", "7",
        ],
    )

    main()

    assert captured == {
        "data_dir": str(tmp_path), "out_path": str(tmp_path / "model.joblib"),
        "test_size": 0.3, "random_state": 7,
    }


def test_main_uses_documented_defaults(tmp_path, monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "app.ml.train_acoustic.train",
        lambda data_dir, out_path, test_size, random_state: captured.update(
            test_size=test_size, random_state=random_state
        ),
    )
    monkeypatch.setattr(
        "sys.argv", ["train_acoustic", "--data-dir", str(tmp_path), "--out", str(tmp_path / "model.joblib")]
    )

    main()

    assert captured == {"test_size": 0.2, "random_state": 42}
