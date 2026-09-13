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

    (a known-drone-control-link RF signature match, derived from the three
    rf_* columns above via app.rf_signatures.match_rf_signature -- the
    same real signal app.fusion already uses to boost confidence for a
    rule-based RF classification -- is computed automatically, not a
    column you provide)

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
from collections import Counter

from app.models import Classification
from app.rf_signatures import match_rf_signature

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
    center_frequency_mhz = float(row["rf_center_frequency_mhz"]) if row.get("rf_center_frequency_mhz") else None
    bandwidth_mhz = float(row["rf_bandwidth_mhz"]) if row.get("rf_bandwidth_mhz") else None
    frequency_hopping = (row.get("rf_frequency_hopping") or "").strip().lower() in ("1", "true", "yes")
    if center_frequency_mhz is not None:
        features["rf_center_frequency_mhz"] = center_frequency_mhz
    if bandwidth_mhz is not None:
        features["rf_bandwidth_mhz"] = bandwidth_mhz
    if frequency_hopping:
        features["rf_frequency_hopping"] = 1.0
    # Derived the same way app.ml.features.extract_features derives it at
    # inference time from a live Detection's raw_data -- keeping this in
    # sync (rather than expecting a CSV column for it) is what rules out
    # train/serve skew on this feature specifically.
    match = match_rf_signature(
        center_frequency_mhz=center_frequency_mhz,
        bandwidth_mhz=bandwidth_mhz,
        frequency_hopping=frequency_hopping,
    )
    if match.confidence > 0.0:
        features["rf_signature_match_confidence"] = match.confidence
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


def train(
    csv_path: str, out_path: str, *, test_size: float = 0.2, random_state: int = 42, cv_folds: int = 5
) -> None:
    import joblib
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.feature_extraction import DictVectorizer
    from sklearn.metrics import classification_report
    from sklearn.model_selection import StratifiedKFold, cross_val_score, train_test_split
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

    # The stratified train_test_split below needs every label represented
    # at least twice (one to end up in each split) -- surfacing that as a
    # clear, actionable SystemExit here (consistent with the two checks
    # above) beats letting a rare label crash training deep inside
    # sklearn's own ValueError instead.
    label_counts = Counter(labels)
    too_rare = {label: count for label, count in label_counts.items() if count < 2}
    if too_rare:
        raise SystemExit(
            f"Label(s) with too few rows in {csv_path} to split into train/test: {too_rare} -- "
            "need at least 2 rows per label."
        )

    def make_pipeline() -> Pipeline:
        return Pipeline(
            [
                ("vectorizer", DictVectorizer(sparse=False)),
                (
                    "classifier",
                    RandomForestClassifier(
                        n_estimators=100, class_weight="balanced", random_state=random_state
                    ),
                ),
            ]
        )

    # class_weight="balanced" (rather than the default uniform weighting)
    # matters here specifically: real labeled detections are unlikely to
    # arrive evenly split across drone/bird/aircraft/unknown (e.g. a lot
    # more "aircraft" than "drone" at most sites), and without it a
    # classifier can score deceptively well on accuracy alone by mostly
    # just always predicting whichever class is most common.
    pipeline = make_pipeline()

    x_train, x_test, y_train, y_test = train_test_split(
        features_list, labels, test_size=test_size, random_state=random_state, stratify=labels
    )
    pipeline.fit(x_train, y_train)
    predictions = pipeline.predict(x_test)
    print(f"Held-out evaluation ({len(x_test)} of {len(features_list)} rows):")
    print(classification_report(y_test, predictions, zero_division=0))

    # A single train/test split's accuracy is noisy, especially on the
    # small datasets a first real labeled set is likely to be -- k-fold
    # cross-validation (a fresh, unfit pipeline per fold, not the one
    # already fit above) gives a more stable estimate of how the model
    # actually generalizes, not just how it did on one particular split.
    # min(label_counts.values()) is guaranteed >= 2 by the check above, so
    # this always has enough rows per class for at least a 2-fold split.
    effective_folds = min(cv_folds, min(label_counts.values()))
    cv_scores = cross_val_score(
        make_pipeline(), features_list, labels,
        cv=StratifiedKFold(n_splits=effective_folds, shuffle=True, random_state=random_state),
    )
    print(
        f"{effective_folds}-fold cross-validation accuracy: "
        f"{cv_scores.mean():.3f} +/- {cv_scores.std():.3f} (folds: {[round(s, 3) for s in cv_scores]})"
    )

    vectorizer: DictVectorizer = pipeline.named_steps["vectorizer"]
    classifier: RandomForestClassifier = pipeline.named_steps["classifier"]
    importances = sorted(
        zip(vectorizer.get_feature_names_out(), classifier.feature_importances_, strict=True),
        key=lambda pair: -pair[1],
    )
    print("Feature importances (highest first):")
    for name, importance in importances:
        print(f"  {name}: {importance:.3f}")

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


if __name__ == "__main__":  # pragma: no cover -- only executes when this
    # file is run directly as a script (python -m / a shebang), never when
    # imported under pytest, so it is structurally unreachable in-process;
    # main()'s own body is covered by tests that call it directly.
    main()
