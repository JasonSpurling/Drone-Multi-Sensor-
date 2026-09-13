"""app.ml.acoustic_model -- classify_audio() against a real trained model
(produced by app.ml.train_acoustic, same trivially-separable synthetic
fixture as tests/test_train_acoustic.py) and its fallback behavior when
unconfigured/misconfigured. See app/ml/__init__.py for why the fixture
data here isn't real training data.
"""

import random
import struct
import wave
from pathlib import Path
from typing import ClassVar

import numpy as np
import pytest

import app.ml.acoustic_model as acoustic_model

pytest.importorskip("sklearn")

SAMPLE_RATE_HZ = 8000


@pytest.fixture(autouse=True)
def _reset_cache():
    acoustic_model.reset_cache_for_tests()
    yield
    acoustic_model.reset_cache_for_tests()


def _tone(frequency_hz: float, duration_s: float = 0.3, seed: int = 0) -> np.ndarray:
    rng = random.Random(seed)
    n_samples = int(duration_s * SAMPLE_RATE_HZ)
    t = np.arange(n_samples) / SAMPLE_RATE_HZ
    signal = np.sin(2 * np.pi * frequency_hz * t)
    noise = np.array([0.05 * (rng.random() * 2 - 1) for _ in range(n_samples)])
    return np.clip(signal + noise, -1.0, 1.0)


def _write_tone_wav(path: Path, frequency_hz: float, seed: int = 0) -> None:
    samples = _tone(frequency_hz, seed=seed)
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(SAMPLE_RATE_HZ)
        f.writeframes(b"".join(struct.pack("<h", int(s * 30000)) for s in samples))


def _train_a_real_model(tmp_path):
    from app.ml.train_acoustic import train

    rng = random.Random(1234)
    for label, (low_hz, high_hz) in [("drone", (100.0, 160.0)), ("bird", (2800.0, 3600.0))]:
        label_dir = tmp_path / label
        label_dir.mkdir()
        for i in range(10):
            freq = rng.uniform(low_hz, high_hz)
            _write_tone_wav(label_dir / f"{i}.wav", freq, seed=rng.randint(0, 1_000_000))
    model_path = tmp_path / "model.joblib"
    train(str(tmp_path), str(model_path), test_size=0.3, random_state=0)
    return model_path


def test_is_configured_reflects_acoustic_ml_model_path(monkeypatch):
    monkeypatch.setattr(acoustic_model, "ACOUSTIC_ML_MODEL_PATH", "")
    assert acoustic_model.is_configured() is False
    monkeypatch.setattr(acoustic_model, "ACOUSTIC_ML_MODEL_PATH", "/some/path.joblib")
    assert acoustic_model.is_configured() is True


def test_classify_audio_returns_none_when_unconfigured(monkeypatch):
    monkeypatch.setattr(acoustic_model, "ACOUSTIC_ML_MODEL_PATH", "")
    assert acoustic_model.classify_audio(_tone(120.0), SAMPLE_RATE_HZ) is None


def test_classify_audio_returns_none_and_warns_when_the_configured_file_is_missing(monkeypatch, caplog):
    monkeypatch.setattr(acoustic_model, "ACOUSTIC_ML_MODEL_PATH", "/no/such/model.joblib")
    assert acoustic_model.classify_audio(_tone(120.0), SAMPLE_RATE_HZ) is None
    assert "does not exist" in caplog.text


def test_classify_audio_returns_none_for_audio_too_short_for_one_frame(tmp_path, monkeypatch):
    model_path = _train_a_real_model(tmp_path)
    monkeypatch.setattr(acoustic_model, "ACOUSTIC_ML_MODEL_PATH", str(model_path))
    assert acoustic_model.classify_audio(np.zeros(5), SAMPLE_RATE_HZ) is None


def test_classify_audio_uses_a_real_trained_model(tmp_path, monkeypatch):
    monkeypatch.setattr(acoustic_model, "ACOUSTIC_ML_CONFIDENCE_THRESHOLD", 0.0)
    model_path = _train_a_real_model(tmp_path)
    monkeypatch.setattr(acoustic_model, "ACOUSTIC_ML_MODEL_PATH", str(model_path))

    # Averaged over several fresh test tones per class, not one hand-picked
    # frequency -- a single pure tone's MFCC features can land as an
    # outlier depending on how its frequency happens to align with the
    # FFT's bin grid (see tests/test_train_acoustic.py's fixture comment);
    # averaging is what actually proves the model learned the real
    # pitch-based pattern, without being fragile to that per-tone luck.
    rng = random.Random(42)
    drone_confidences = [
        acoustic_model.classify_audio(_tone(rng.uniform(100.0, 160.0), seed=i), SAMPLE_RATE_HZ) for i in range(5)
    ]
    bird_confidences = [
        acoustic_model.classify_audio(_tone(rng.uniform(2800.0, 3600.0), seed=100 + i), SAMPLE_RATE_HZ)
        for i in range(5)
    ]
    assert all(c is not None for c in drone_confidences + bird_confidences)
    assert (sum(drone_confidences) / 5) > (sum(bird_confidences) / 5)


def test_classify_audio_returns_the_drone_class_probability_not_the_top_class(tmp_path, monkeypatch):
    # Even for audio the model's top prediction is "bird" for, the return
    # value must be the DRONE-class probability specifically (small, but
    # present) -- not e.g. "1 - top_probability" or some other proxy --
    # since that's what this app's confidence field means everywhere else.
    model_path = _train_a_real_model(tmp_path)
    monkeypatch.setattr(acoustic_model, "ACOUSTIC_ML_MODEL_PATH", str(model_path))
    monkeypatch.setattr(acoustic_model, "ACOUSTIC_ML_CONFIDENCE_THRESHOLD", 0.0)

    import joblib

    model = joblib.load(model_path)
    bird_signal = _tone(3200.0, seed=998)
    from app.acoustic_features import extract_mfcc, summarize_mfcc

    features = summarize_mfcc(extract_mfcc(bird_signal, SAMPLE_RATE_HZ))
    expected_drone_probability = float(
        model.predict_proba([features])[0][list(model.classes_).index("drone")]
    )

    result = acoustic_model.classify_audio(bird_signal, SAMPLE_RATE_HZ)
    assert result == pytest.approx(expected_drone_probability)


def test_classify_audio_caches_the_loaded_model_across_calls(tmp_path, monkeypatch):
    model_path = _train_a_real_model(tmp_path)
    monkeypatch.setattr(acoustic_model, "ACOUSTIC_ML_MODEL_PATH", str(model_path))

    calls = []
    import joblib

    original_joblib_load = joblib.load

    def _counting_load(path):
        calls.append(path)
        return original_joblib_load(path)

    monkeypatch.setattr(joblib, "load", _counting_load)

    acoustic_model.classify_audio(_tone(120.0), SAMPLE_RATE_HZ)
    acoustic_model.classify_audio(_tone(120.0), SAMPLE_RATE_HZ)
    assert len(calls) == 1


def test_reset_cache_for_tests_forces_a_reload(tmp_path, monkeypatch):
    model_path = _train_a_real_model(tmp_path)
    monkeypatch.setattr(acoustic_model, "ACOUSTIC_ML_MODEL_PATH", str(model_path))

    import joblib

    calls = []
    original_joblib_load = joblib.load

    def _counting_load(path):
        calls.append(path)
        return original_joblib_load(path)

    monkeypatch.setattr(joblib, "load", _counting_load)

    acoustic_model.classify_audio(_tone(120.0), SAMPLE_RATE_HZ)
    acoustic_model.reset_cache_for_tests()
    acoustic_model.classify_audio(_tone(120.0), SAMPLE_RATE_HZ)
    assert len(calls) == 2


def test_classify_audio_returns_none_when_top_class_probability_is_below_threshold(tmp_path, monkeypatch):
    model_path = _train_a_real_model(tmp_path)
    monkeypatch.setattr(acoustic_model, "ACOUSTIC_ML_MODEL_PATH", str(model_path))
    monkeypatch.setattr(acoustic_model, "ACOUSTIC_ML_CONFIDENCE_THRESHOLD", 1.01)
    assert acoustic_model.classify_audio(_tone(120.0), SAMPLE_RATE_HZ) is None


class _FakeModelWithoutDroneClass:
    """A model that was never trained on any "drone" examples -- e.g. a
    stale model file trained against a different label set entirely.
    classify_audio() must fall back gracefully (None), not crash on a
    missing class.
    """

    classes_: ClassVar = ["bird", "aircraft"]

    def predict_proba(self, features):
        return np.array([[0.6, 0.4]])


def _use_fake_cached_model(tmp_path, monkeypatch, model):
    model_path = tmp_path / "model.joblib"
    model_path.write_bytes(b"")
    monkeypatch.setattr(acoustic_model, "ACOUSTIC_ML_MODEL_PATH", str(model_path))
    monkeypatch.setattr(acoustic_model, "_model_cache", model)
    monkeypatch.setattr(acoustic_model, "_model_cache_path", str(model_path))


def test_classify_audio_returns_none_when_the_model_has_no_drone_class(tmp_path, monkeypatch):
    _use_fake_cached_model(tmp_path, monkeypatch, _FakeModelWithoutDroneClass())
    assert acoustic_model.classify_audio(_tone(120.0), SAMPLE_RATE_HZ) is None


class _FakeModelThatRaises:
    classes_: ClassVar = ["drone", "bird"]

    def predict_proba(self, features):
        raise ValueError("model input shape mismatch")


def test_classify_audio_returns_none_and_warns_for_a_model_producing_an_unusable_prediction(
    tmp_path, monkeypatch, caplog
):
    _use_fake_cached_model(tmp_path, monkeypatch, _FakeModelThatRaises())

    with caplog.at_level("WARNING"):
        assert acoustic_model.classify_audio(_tone(120.0), SAMPLE_RATE_HZ) is None

    assert "unusable prediction" in caplog.text
