"""Loads a trained model (DRONE_ML_MODEL_PATH, produced by app/ml/train.py)
and offers it as an optional first opinion for app.fusion._detection_label
-- see app/ml/__init__.py for the full picture of why this ships as
scaffolding, not a trained model.

scikit-learn is only imported inside _load_model() (requirements-ml.txt,
not requirements.txt, the same lazy-import pattern app/oidc.py uses for
authlib) -- predict() returns None immediately, before ever touching
scikit-learn, whenever DRONE_ML_MODEL_PATH is unset (the default), so a
deployment that never configures this never needs it installed.

SECURITY: joblib.load() (used below) deserializes via pickle under the
hood, which can execute arbitrary code for a maliciously crafted file --
the same class of risk as unpickling any other untrusted data. Bandit's
static analysis doesn't flag this (it only recognizes pickle.load/loads
directly, not joblib's wrapper), so it isn't caught automatically the way
a raw pickle.load() call would be. Only ever point DRONE_ML_MODEL_PATH at
a file *you* trained (via app/ml/train.py) or otherwise fully trust --
never at a downloaded or third-party-supplied model file.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.config import ML_MODEL_PATH
from app.ml.features import extract_features
from app.models import Classification, Detection

logger = logging.getLogger(__name__)

_model_cache: Any = None
_model_cache_path: str | None = None


def is_configured() -> bool:
    return bool(ML_MODEL_PATH)


def _load_model() -> Any | None:
    """None (never raises) if there's nothing to load -- unset, or a
    configured path that doesn't exist -- so predict() can always fall
    back to rule-based classification instead of crashing detection
    ingest over a missing/misconfigured model file.
    """
    global _model_cache, _model_cache_path
    if not ML_MODEL_PATH:
        return None
    if not Path(ML_MODEL_PATH).is_file():
        logger.warning(
            "DRONE_ML_MODEL_PATH=%r does not exist -- falling back to rule-based classification",
            ML_MODEL_PATH,
        )
        return None
    if _model_cache is not None and _model_cache_path == ML_MODEL_PATH:
        return _model_cache

    import joblib

    _model_cache = joblib.load(ML_MODEL_PATH)
    _model_cache_path = ML_MODEL_PATH
    logger.info("Loaded ML classification model from %s", ML_MODEL_PATH)
    return _model_cache


def predict(detection: Detection) -> Classification | None:
    """None means "no opinion" -- not configured, the model file is
    missing, or the model produced a label this app doesn't recognize --
    in every one of those cases the caller (app.fusion._detection_label)
    falls back to the rule-based classifier; this never raises out into
    the detection-ingest path.
    """
    model = _load_model()
    if model is None:
        return None
    features = extract_features(detection)
    try:
        raw_label = model.predict([features])[0]
        return Classification(raw_label)
    except (ValueError, IndexError, KeyError) as exc:
        logger.warning("ML model produced an unusable prediction (%s) -- falling back to rule-based", exc)
        return None


def reset_cache_for_tests() -> None:
    """Without this, a test pointing DRONE_ML_MODEL_PATH at its own model
    file would see a previous test's cached model instead of loading the
    new one -- same reasoning as app.oidc.reset_for_tests().
    """
    global _model_cache, _model_cache_path
    _model_cache = None
    _model_cache_path = None
