"""Loads a trained acoustic classifier (DRONE_ACOUSTIC_ML_MODEL_PATH,
produced by app/ml/train_acoustic.py) and offers a confidence opinion for
app/adapters/acoustic_array_bridge.py to use in place of its manual,
operator-supplied --confidence -- see app/ml/model.py for the near-
identical pattern this mirrors. It's a separate module (not folded into
app.ml.model) because it classifies raw audio via MFCC features
(app/acoustic_features.py), not an already-formed Detection's metadata,
so it can't share model.py's Detection-shaped predict().

scikit-learn is only imported inside _load_model() -- the same lazy-
import, "never needed unless configured" contract as app.ml.model.

SECURITY: see app/ml/model.py's docstring -- the same joblib.load()
pickle-deserialization risk applies here (bandit doesn't flag joblib's
wrapper the way it would a raw pickle.load()). Only ever point
DRONE_ACOUSTIC_ML_MODEL_PATH at a file you trained yourself.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np

from app.acoustic_features import extract_mfcc, summarize_mfcc
from app.config import ACOUSTIC_ML_CONFIDENCE_THRESHOLD, ACOUSTIC_ML_MODEL_PATH
from app.models import Classification

logger = logging.getLogger(__name__)

_model_cache: Any = None
_model_cache_path: str | None = None


def is_configured() -> bool:
    return bool(ACOUSTIC_ML_MODEL_PATH)


def _load_model() -> Any | None:
    """None (never raises) if there's nothing to load -- unset, or a
    configured path that doesn't exist -- so classify_audio() can always
    fall back to the bridge's manual confidence instead of crashing over
    a missing/misconfigured model file.
    """
    global _model_cache, _model_cache_path
    if not ACOUSTIC_ML_MODEL_PATH:
        return None
    if not Path(ACOUSTIC_ML_MODEL_PATH).is_file():
        logger.warning(
            "DRONE_ACOUSTIC_ML_MODEL_PATH=%r does not exist -- falling back to manual confidence",
            ACOUSTIC_ML_MODEL_PATH,
        )
        return None
    if _model_cache is not None and _model_cache_path == ACOUSTIC_ML_MODEL_PATH:
        return _model_cache

    import joblib

    _model_cache = joblib.load(ACOUSTIC_ML_MODEL_PATH)
    _model_cache_path = ACOUSTIC_ML_MODEL_PATH
    logger.info("Loaded acoustic classification model from %s", ACOUSTIC_ML_MODEL_PATH)
    return _model_cache


def classify_audio(samples: np.ndarray, sample_rate_hz: float) -> float | None:
    """None means "no opinion" -- not configured, missing model file, a
    recording too short to extract any MFCC frames from, or (see
    ACOUSTIC_ML_CONFIDENCE_THRESHOLD) the model's own top-class
    probability wasn't high enough to trust -- in every case the caller
    falls back to its operator-supplied --confidence. Never raises.

    Returns the model's predicted probability specifically for the DRONE
    class, not just its top prediction's probability -- consistent with
    how confidence is used everywhere else in this app: a single "how
    likely is this a drone" number, not a 4-way label. A model whose top
    prediction is BIRD or AIRCRAFT still has *some* probability mass on
    DRONE (even if low); that's the number this app's confidence field
    means, not "the model's own best guess was right."
    """
    model = _load_model()
    if model is None:
        return None
    features = summarize_mfcc(extract_mfcc(samples, sample_rate_hz))
    if not features:
        return None
    try:
        probabilities = model.predict_proba([features])[0]
        classes = list(model.classes_)
        if Classification.DRONE.value not in classes:
            return None
        if float(probabilities.max()) < ACOUSTIC_ML_CONFIDENCE_THRESHOLD:
            return None
        return float(probabilities[classes.index(Classification.DRONE.value)])
    except (ValueError, IndexError, KeyError, AttributeError) as exc:
        logger.warning(
            "Acoustic ML model produced an unusable prediction (%s) -- falling back to manual confidence", exc
        )
        return None


def reset_cache_for_tests() -> None:
    """Without this, a test pointing DRONE_ACOUSTIC_ML_MODEL_PATH at its
    own model file would see a previous test's cached model instead of
    loading the new one -- same reasoning as app.ml.model's equivalent.
    """
    global _model_cache, _model_cache_path
    _model_cache = None
    _model_cache_path = None
