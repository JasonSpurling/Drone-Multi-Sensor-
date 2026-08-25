"""app.ml.train -- tested against small, trivially-separable synthetic
CSVs (obviously not real drone/bird/aircraft sensor data; see app/ml/'s
own docstring for why this repo has none). These rows exist purely to
prove the training *pipeline* actually works end-to-end (CSV parsing ->
feature extraction -> a real scikit-learn Pipeline.fit() -> a real
held-out evaluation -> a real joblib-loadable model file) -- not to claim
the resulting model is fit for real classification.
"""

import csv

import pytest

from app.ml.train import load_csv, train

pytest.importorskip("sklearn")

# Two trivially-separable classes on `confidence` alone -- high confidence
# always "drone", low confidence always "bird" -- so a trained classifier
# is verifiably able to learn *something* real from this feature, without
# this being anything resembling genuine labeled sensor data.
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


def _write_csv(path, rows):
    fieldnames = sorted({key for row in rows for key in row})
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def test_load_csv_parses_features_and_labels(tmp_path):
    csv_path = tmp_path / "labeled.csv"
    _write_csv(csv_path, _ROWS)

    features_list, labels = load_csv(str(csv_path))

    assert len(features_list) == len(_ROWS) == len(labels)
    assert features_list[0] == {"sensor_type": "camera", "confidence": 0.95}
    assert labels[0] == "drone"


def test_load_csv_parses_optional_rf_and_altitude_columns(tmp_path):
    csv_path = tmp_path / "labeled.csv"
    _write_csv(
        csv_path,
        [
            {
                "sensor_type": "rf", "confidence": "0.8", "label": "drone",
                "altitude_m": "50", "rf_center_frequency_mhz": "5800",
                "rf_bandwidth_mhz": "10", "rf_frequency_hopping": "true",
            }
        ],
    )
    features_list, _ = load_csv(str(csv_path))
    assert features_list[0] == {
        "sensor_type": "rf", "confidence": 0.8, "altitude_m": 50.0,
        "rf_center_frequency_mhz": 5800.0, "rf_bandwidth_mhz": 10.0, "rf_frequency_hopping": 1.0,
    }


def test_train_rejects_too_few_rows(tmp_path):
    csv_path = tmp_path / "tiny.csv"
    _write_csv(csv_path, _ROWS[:3])
    with pytest.raises(SystemExit, match="Only 3"):
        train(str(csv_path), str(tmp_path / "model.joblib"))


def test_train_rejects_unrecognized_labels(tmp_path):
    csv_path = tmp_path / "labeled.csv"
    _write_csv(csv_path, [*_ROWS, {"sensor_type": "camera", "confidence": "0.9", "label": "friendly"}])
    with pytest.raises(SystemExit, match="friendly"):
        train(str(csv_path), str(tmp_path / "model.joblib"))


def test_train_produces_a_loadable_model_that_predicts_sensibly(tmp_path, capsys):
    csv_path = tmp_path / "labeled.csv"
    _write_csv(csv_path, _ROWS)
    model_path = tmp_path / "model.joblib"

    train(str(csv_path), str(model_path), test_size=0.3, random_state=0)

    assert model_path.is_file()
    output = capsys.readouterr().out
    assert "Saved model to" in output
    assert "precision" in output  # classification_report printed

    import joblib

    model = joblib.load(model_path)
    # The trivially-separable pattern this fixture data encodes: high
    # confidence -> drone.
    assert model.predict([{"sensor_type": "camera", "confidence": 0.97}])[0] == "drone"
    assert model.predict([{"sensor_type": "camera", "confidence": 0.02}])[0] == "bird"
