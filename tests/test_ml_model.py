"""app.ml.model -- predict() against a real trained model (produced by
app.ml.train, same trivially-separable synthetic fixture as
tests/test_ml_train.py) and its fallback behavior when unconfigured/
misconfigured. See app/ml/__init__.py for why the fixture data here isn't
real training data.
"""

import csv

import pytest

import app.ml.model as ml_model
from app.models import Classification, Detection, SensorType

pytest.importorskip("sklearn")

_ROWS = [
    {"sensor_type": "camera", "confidence": "0.95", "label": "drone"},
    {"sensor_type": "camera", "confidence": "0.92", "label": "drone"},
    {"sensor_type": "radar", "confidence": "0.9", "label": "drone"},
    {"sensor_type": "radar", "confidence": "0.88", "label": "drone"},
    {"sensor_type": "camera", "confidence": "0.93", "label": "drone"},
    {"sensor_type": "camera", "confidence": "0.1", "label": "bird"},
    {"sensor_type": "camera", "confidence": "0.12", "label": "bird"},
    {"sensor_type": "acoustic", "confidence": "0.08", "label": "bird"},
    {"sensor_type": "acoustic", "confidence": "0.15", "label": "bird"},
    {"sensor_type": "camera", "confidence": "0.11", "label": "bird"},
    {"sensor_type": "radar", "confidence": "0.96", "label": "drone"},
    {"sensor_type": "acoustic", "confidence": "0.05", "label": "bird"},
]


@pytest.fixture(autouse=True)
def _reset_cache():
    ml_model.reset_cache_for_tests()
    yield
    ml_model.reset_cache_for_tests()


def _detection(**overrides) -> Detection:
    defaults = {
        "sensor_id": "s1", "sensor_type": SensorType.CAMERA,
        "latitude": 51.5, "longitude": -0.1, "confidence": 0.9,
    }
    defaults.update(overrides)
    return Detection(**defaults)


def _train_a_real_model(tmp_path):
    from app.ml.train import train

    csv_path = tmp_path / "labeled.csv"
    fieldnames = sorted({key for row in _ROWS for key in row})
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(_ROWS)
    model_path = tmp_path / "model.joblib"
    train(str(csv_path), str(model_path), test_size=0.3, random_state=0)
    return model_path


def test_is_configured_reflects_ml_model_path(monkeypatch):
    monkeypatch.setattr(ml_model, "ML_MODEL_PATH", "")
    assert ml_model.is_configured() is False
    monkeypatch.setattr(ml_model, "ML_MODEL_PATH", "/some/path.joblib")
    assert ml_model.is_configured() is True


def test_predict_returns_none_when_unconfigured(monkeypatch):
    monkeypatch.setattr(ml_model, "ML_MODEL_PATH", "")
    assert ml_model.predict(_detection()) is None


def test_predict_returns_none_and_warns_when_the_configured_file_is_missing(monkeypatch, caplog):
    monkeypatch.setattr(ml_model, "ML_MODEL_PATH", "/no/such/model.joblib")
    assert ml_model.predict(_detection()) is None
    assert "does not exist" in caplog.text


def test_predict_uses_a_real_trained_model(tmp_path, monkeypatch):
    model_path = _train_a_real_model(tmp_path)
    monkeypatch.setattr(ml_model, "ML_MODEL_PATH", str(model_path))

    assert ml_model.predict(_detection(confidence=0.97)) == Classification.DRONE
    assert ml_model.predict(_detection(confidence=0.02)) == Classification.BIRD


def test_predict_caches_the_loaded_model_across_calls(tmp_path, monkeypatch):
    model_path = _train_a_real_model(tmp_path)
    monkeypatch.setattr(ml_model, "ML_MODEL_PATH", str(model_path))

    calls = []
    real_load = ml_model._load_model

    import joblib

    original_joblib_load = joblib.load

    def _counting_load(path):
        calls.append(path)
        return original_joblib_load(path)

    monkeypatch.setattr(joblib, "load", _counting_load)

    ml_model.predict(_detection())
    ml_model.predict(_detection())
    assert len(calls) == 1  # loaded once, reused for the second predict()
    assert real_load is ml_model._load_model  # sanity: didn't monkeypatch the wrong thing


def test_reset_cache_for_tests_forces_a_reload(tmp_path, monkeypatch):
    model_path = _train_a_real_model(tmp_path)
    monkeypatch.setattr(ml_model, "ML_MODEL_PATH", str(model_path))

    import joblib

    calls = []
    original_joblib_load = joblib.load

    def _counting_load(path):
        calls.append(path)
        return original_joblib_load(path)

    monkeypatch.setattr(joblib, "load", _counting_load)

    ml_model.predict(_detection())
    ml_model.reset_cache_for_tests()
    ml_model.predict(_detection())
    assert len(calls) == 2


def test_predict_returns_none_when_top_class_probability_is_below_threshold(tmp_path, monkeypatch):
    # An impossible-to-clear threshold (max probability is 1.0) proves the
    # gate itself works regardless of exactly how confident this
    # particular model/input happens to be -- see ML_CONFIDENCE_THRESHOLD's
    # docstring in app/config.py for why this exists at all.
    model_path = _train_a_real_model(tmp_path)
    monkeypatch.setattr(ml_model, "ML_MODEL_PATH", str(model_path))
    monkeypatch.setattr(ml_model, "ML_CONFIDENCE_THRESHOLD", 1.01)

    assert ml_model.predict(_detection(confidence=0.97)) is None
    assert ml_model.predict(_detection(confidence=0.02)) is None


def test_predict_returns_a_label_when_threshold_is_permissive(tmp_path, monkeypatch):
    model_path = _train_a_real_model(tmp_path)
    monkeypatch.setattr(ml_model, "ML_MODEL_PATH", str(model_path))
    monkeypatch.setattr(ml_model, "ML_CONFIDENCE_THRESHOLD", 0.0)

    assert ml_model.predict(_detection(confidence=0.97)) == Classification.DRONE
    assert ml_model.predict(_detection(confidence=0.02)) == Classification.BIRD
