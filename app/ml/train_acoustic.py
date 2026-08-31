"""Trains an acoustic classifier from labeled WAV recordings and saves it
to a file DRONE_ACOUSTIC_ML_MODEL_PATH can point at -- see app/ml/__init__.py
for why this repo ships no such recordings and no trained model: there is
no real labeled drone/bird/aircraft/unknown rotor-audio dataset here to
train from honestly.

Expected --data-dir layout (one subdirectory per label, same convention
as a standard image-classification dataset):

    data-dir/
      drone/*.wav
      bird/*.wav
      aircraft/*.wav
      unknown/*.wav

Each subdirectory name must be one of app.models.TrainableLabel's values
(the same trainable label set app/ml/train.py uses -- not "friendly": see
that module's docstring for why). Each .wav is read as PCM audio via the
standard library's `wave` module (no new dependency for this); a
multi-channel file is averaged to one channel first, the same mixdown
app/adapters/acoustic_array_bridge.py's watch() does before MFCC
extraction, so training sees the same signal shape inference will.

Usage:
    python -m app.ml.train_acoustic --data-dir data/acoustic --out acoustic_model.joblib
"""

from __future__ import annotations

import argparse
import wave
from collections import Counter
from pathlib import Path

import numpy as np

from app.acoustic_features import extract_mfcc, summarize_mfcc
from app.models import TrainableLabel

_TRAINABLE_LABELS = {label.value for label in TrainableLabel}

# Signed-integer WAV sample widths this reads, mapped to the divisor that
# turns a raw sample into a float in [-1.0, 1.0] -- the same normalized
# range a live recording from sounddevice (float32, already normalized)
# arrives in, so a trained model sees consistent input regardless of
# which path (training WAV file vs. live sd.rec() block) produced it.
_SAMPLE_WIDTH_SCALE = {1: 128.0, 2: 32768.0, 4: 2147483648.0}


def load_wav_mono(path: Path) -> tuple[np.ndarray, float]:
    """Reads one WAV file and returns (samples, sample_rate_hz), samples
    as a 1D float array in [-1.0, 1.0], averaged across channels if the
    file has more than one.
    """
    with wave.open(str(path), "rb") as wav_file:
        n_channels = wav_file.getnchannels()
        sample_width = wav_file.getsampwidth()
        sample_rate_hz = float(wav_file.getframerate())
        raw = wav_file.readframes(wav_file.getnframes())

    if sample_width not in _SAMPLE_WIDTH_SCALE:
        raise SystemExit(f"{path}: unsupported WAV sample width {sample_width} bytes (need 8/16/32-bit PCM)")
    dtype = {1: np.uint8, 2: np.int16, 4: np.int32}[sample_width]
    samples = np.frombuffer(raw, dtype=dtype).astype(np.float64)
    if sample_width == 1:
        samples -= 128.0  # 8-bit WAV PCM is unsigned, centered on 128
    samples /= _SAMPLE_WIDTH_SCALE[sample_width]

    if n_channels > 1:
        samples = samples.reshape(-1, n_channels).mean(axis=1)
    return samples, sample_rate_hz


def load_dataset(data_dir: str) -> tuple[list[dict[str, float]], list[str]]:
    features_list: list[dict[str, float]] = []
    labels: list[str] = []
    root = Path(data_dir)
    if not root.is_dir():
        raise SystemExit(f"--data-dir {data_dir!r} is not a directory")

    for label_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        if label_dir.name not in _TRAINABLE_LABELS:
            raise SystemExit(
                f"{label_dir} is not a recognized label -- expected a subdirectory named one of "
                f"{sorted(_TRAINABLE_LABELS)}"
            )
        for wav_path in sorted(label_dir.glob("*.wav")):
            samples, sample_rate_hz = load_wav_mono(wav_path)
            features = summarize_mfcc(extract_mfcc(samples, sample_rate_hz))
            if not features:
                print(f"Skipping {wav_path}: too short to extract any MFCC frames from")
                continue
            features_list.append(features)
            labels.append(label_dir.name)
    return features_list, labels


def train(
    data_dir: str, out_path: str, *, test_size: float = 0.2, random_state: int = 42, cv_folds: int = 5
) -> None:
    import joblib
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.feature_extraction import DictVectorizer
    from sklearn.metrics import classification_report
    from sklearn.model_selection import StratifiedKFold, cross_val_score, train_test_split
    from sklearn.pipeline import Pipeline

    features_list, labels = load_dataset(data_dir)
    if len(features_list) < 10:
        raise SystemExit(
            f"Only {len(features_list)} labeled recording(s) under {data_dir} -- need real labeled "
            "audio to train a meaningful model, not a placeholder-sized sample."
        )

    label_counts = Counter(labels)
    too_rare = {label: count for label, count in label_counts.items() if count < 2}
    if too_rare:
        raise SystemExit(
            f"Label(s) with too few recordings under {data_dir} to split into train/test: {too_rare} -- "
            "need at least 2 recordings per label."
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

    # class_weight="balanced" for the same reason app.ml.train's identical
    # choice explains: real labeled recordings are unlikely to arrive
    # evenly split across labels, and without it a classifier can score
    # deceptively well by mostly predicting whichever class is most common.
    pipeline = make_pipeline()

    x_train, x_test, y_train, y_test = train_test_split(
        features_list, labels, test_size=test_size, random_state=random_state, stratify=labels
    )
    pipeline.fit(x_train, y_train)
    predictions = pipeline.predict(x_test)
    print(f"Held-out evaluation ({len(x_test)} of {len(features_list)} recordings):")
    print(classification_report(y_test, predictions, zero_division=0))

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
    print(f"Saved model to {out_path} -- point DRONE_ACOUSTIC_ML_MODEL_PATH at it to use it.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", required=True, help="Directory of label subdirectories, each full of .wav files")
    parser.add_argument("--out", required=True, help="Where to save the trained model (joblib format)")
    parser.add_argument("--test-size", type=float, default=0.2, help="Fraction held out for evaluation")
    parser.add_argument("--random-state", type=int, default=42)
    args = parser.parse_args()
    train(args.data_dir, args.out, test_size=args.test_size, random_state=args.random_state)


if __name__ == "__main__":
    main()
