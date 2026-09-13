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

from app.ml.train import load_csv, main, train

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
    # 5800 MHz / 10 MHz bandwidth / hopping matches a real built-in
    # signature (see app/rf_signatures.py) -- rf_signature_match_confidence
    # is derived automatically, not something the CSV itself provides.
    assert features_list[0] == {
        "sensor_type": "rf", "confidence": 0.8, "altitude_m": 50.0,
        "rf_center_frequency_mhz": 5800.0, "rf_bandwidth_mhz": 10.0, "rf_frequency_hopping": 1.0,
        "rf_signature_match_confidence": 0.9,
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


def test_train_prints_cross_validation_accuracy(tmp_path, capsys):
    csv_path = tmp_path / "labeled.csv"
    _write_csv(csv_path, _ROWS)  # 6 drone / 6 bird rows -- enough for 5-fold CV
    model_path = tmp_path / "model.joblib"

    train(str(csv_path), str(model_path), test_size=0.3, random_state=0)

    output = capsys.readouterr().out
    assert "5-fold cross-validation accuracy" in output


def test_train_rejects_a_label_with_too_few_rows_to_split(tmp_path):
    csv_path = tmp_path / "labeled.csv"
    # 9 drone rows + 1 bird row -- the stratified train/test split (and
    # cross-validation) both need every label represented at least twice.
    rows = [{"sensor_type": "camera", "confidence": "0.9", "label": "drone"} for _ in range(9)]
    rows.append({"sensor_type": "camera", "confidence": "0.1", "label": "bird"})
    _write_csv(csv_path, rows)

    with pytest.raises(SystemExit, match="bird"):
        train(str(csv_path), str(tmp_path / "model.joblib"), test_size=0.3, random_state=0)


def test_train_prints_feature_importances(tmp_path, capsys):
    csv_path = tmp_path / "labeled.csv"
    _write_csv(csv_path, _ROWS)
    model_path = tmp_path / "model.joblib"

    train(str(csv_path), str(model_path), test_size=0.3, random_state=0)

    output = capsys.readouterr().out
    assert "Feature importances" in output
    assert "confidence" in output


def test_train_uses_balanced_class_weight(tmp_path):
    csv_path = tmp_path / "labeled.csv"
    _write_csv(csv_path, _ROWS)
    model_path = tmp_path / "model.joblib"

    train(str(csv_path), str(model_path), test_size=0.3, random_state=0)

    import joblib

    model = joblib.load(model_path)
    assert model.named_steps["classifier"].class_weight == "balanced"


def test_main_parses_args_and_invokes_train(tmp_path, monkeypatch):
    csv_path = tmp_path / "labeled.csv"
    _write_csv(csv_path, _ROWS)
    model_path = tmp_path / "model.joblib"
    captured = {}
    monkeypatch.setattr(
        "app.ml.train.train",
        lambda csv_path, out_path, test_size, random_state: captured.update(
            csv_path=csv_path, out_path=out_path, test_size=test_size, random_state=random_state
        ),
    )
    monkeypatch.setattr(
        "sys.argv",
        ["train", "--csv", str(csv_path), "--out", str(model_path), "--test-size", "0.3", "--random-state", "7"],
    )

    main()

    assert captured == {
        "csv_path": str(csv_path), "out_path": str(model_path), "test_size": 0.3, "random_state": 7,
    }


def test_main_uses_documented_defaults(tmp_path, monkeypatch):
    csv_path = tmp_path / "labeled.csv"
    _write_csv(csv_path, _ROWS)
    model_path = tmp_path / "model.joblib"
    captured = {}
    monkeypatch.setattr(
        "app.ml.train.train",
        lambda csv_path, out_path, test_size, random_state: captured.update(
            test_size=test_size, random_state=random_state
        ),
    )
    monkeypatch.setattr("sys.argv", ["train", "--csv", str(csv_path), "--out", str(model_path)])

    main()

    assert captured == {"test_size": 0.2, "random_state": 42}
