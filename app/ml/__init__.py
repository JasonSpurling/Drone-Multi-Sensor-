"""Optional ML-based per-detection classification -- training/inference
*scaffolding* only. This app ships no trained model and no labeled
dataset, because there is no real captured drone/bird/aircraft sensor
data anywhere in this repo or sandbox to train one from honestly (the
same reasoning app/rf_signatures.py's module docstring gives for shipping
no bundled RF signature data). Fabricating synthetic training data just
to have a "trained" model would be worse than not having one -- it would
look like real ML while actually encoding nothing but guesses.

What's here instead is real, working infrastructure ready to train the
moment genuine labeled data exists:

- app/ml/features.py -- deterministic Detection -> feature-dict
  extraction, shared by training and inference so there's no train/serve
  skew between the two.
- app/ml/train.py -- `python -m app.ml.train --csv labeled.csv --out
  model.joblib` trains a real scikit-learn Pipeline (DictVectorizer +
  RandomForestClassifier) and reports held-out accuracy/precision/recall.
- app/ml/model.py -- loads a trained model (DRONE_ML_MODEL_PATH) and
  wires it into app.fusion._detection_label as an optional first opinion,
  ahead of the existing rule-based app.classification.classify(). Unset
  (the default) or pointing at a missing file: classification behaves
  exactly as it did before this package existed, with zero change.

scikit-learn is a genuinely optional dependency (requirements-ml.txt, not
requirements.txt) -- only imported inside app.ml.model's _load_model() and
app.ml.train, both gated so a deployment that never sets
DRONE_ML_MODEL_PATH or runs the training script never needs it installed.
"""
