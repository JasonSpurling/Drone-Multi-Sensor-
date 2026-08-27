"""Trains a classifier from a CSV of labeled detections and saves it to a
file DRONE_ML_MODEL_PATH can point at -- see app/ml/__init__.py for why
this repo ships no such CSV and no trained model: there is no real
labeled drone/bird/aircraft dataset here to train from honestly.

Expected CSV columns:
    sensor_type              one of radar/rf/camera/acoustic/adsb/other
    confidence                0.0-1.0
    altitude_m                 optional
    rf_center_frequency_mhz   optional, RF detections only
    rf_bandwidth_mhz           optional, RF detections only
    rf_frequency_hopping      optional, RF detections only (1/true/yes)
    label                      one of drone/bird/aircraft/unknown -- NOT
                               "friendly": app.allowlist decides that
                               independently of any ML model (an
                               unauthenticated raw_data.operator_id claim
                               isn't a feature to train a classifier on)

Usage:
    python -m app.ml.train --csv labeled_detections.csv --out model.joblib
"""

from __future__ import annotations

import argparse
import csv as csv_module

from app.models import Classification

_TRAINABLE_LABELS = {
    Classification.DRONE.value,
    Classification.BIRD.value,
    Classification.AIRCRAFT.value,
    Classification.UNKNOWN.value,
}


def _row_to_features_and_label(row: dict[str, str]) -> tuple[dict[str, float | str], str]:
    features: dict[str, float | str] = {
        "sensor_type": row["sensor_type"],
        "confidence": float(row["confidence"]),
    }
    if row.get("altitude_m"):
        features["altitude_m"] = float(row["altitude_m"])
    if row.get("rf_center_frequency_mhz"):
        features["rf_center_frequency_mhz"] = float(row["rf_center_frequency_mhz"])
    if row.get("rf_bandwidth_mhz"):
        features["rf_bandwidth_mhz"] = float(row["rf_bandwidth_mhz"])
    if (row.get("rf_frequency_hopping") or "").strip().lower() in ("1", "true", "yes"):
        features["rf_frequency_hopping"] = 1.0
    return features, row["label"]


def load_csv(path: str) -> tuple[list[dict[str, float | str]], list[str]]:
    features_list: list[dict[str, float | str]] = []
    labels: list[str] = []
    with open(path, newline="") as f:
        for row in csv_module.DictReader(f):
            features, label = _row_to_features_and_label(row)
            features_list.append(features)
            labels.append(label)
    return features_list, labels


def train(csv_path: str, out_path: str, *, test_size: float = 0.2, random_state: int = 42) -> None:
    import joblib
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.feature_extraction import DictVectorizer
    from sklearn.metrics import classification_report
    from sklearn.model_selection import train_test_split
    from sklearn.pipeline import Pipeline

    features_list, labels = load_csv(csv_path)
    if len(features_list) < 10:
        raise SystemExit(
            f"Only {len(features_list)} labeled row(s) in {csv_path} -- need real labeled "
            "data to train a meaningful model, not a placeholder-sized sample."
        )

    unrecognized = set(labels) - _TRAINABLE_LABELS
    if unrecognized:
        raise SystemExit(
            f"Unrecognized label(s) in {csv_path}: {sorted(unrecognized)} -- expected one of "
            f"{sorted(_TRAINABLE_LABELS)} (not 'friendly': see this module's docstring)"
        )

    x_train, x_test, y_train, y_test = train_test_split(
        features_list, labels, test_size=test_size, random_state=random_state, stratify=labels
    )

    pipeline = Pipeline(
        [
            ("vectorizer", DictVectorizer(sparse=False)),
            ("classifier", RandomForestClassifier(n_estimators=100, random_state=random_state)),
        ]
    )
    pipeline.fit(x_train, y_train)

    predictions = pipeline.predict(x_test)
    print(f"Held-out evaluation ({len(x_test)} of {len(features_list)} rows):")
    print(classification_report(y_test, predictions, zero_division=0))

    joblib.dump(pipeline, out_path)
    print(f"Saved model to {out_path} -- point DRONE_ML_MODEL_PATH at it to use it.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--csv", required=True, help="Path to a CSV of labeled detections")
    parser.add_argument("--out", required=True, help="Where to save the trained model (joblib format)")
    parser.add_argument("--test-size", type=float, default=0.2, help="Fraction held out for evaluation")
    parser.add_argument("--random-state", type=int, default=42)
    args = parser.parse_args()
    train(args.csv, args.out, test_size=args.test_size, random_state=args.random_state)


if __name__ == "__main__":
    main()
