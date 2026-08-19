"""Centralized configuration, sourced from environment variables with
sensible defaults so the app runs out of the box with no setup.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

HOST = os.getenv("DRONE_HOST", "127.0.0.1")
PORT = int(os.getenv("DRONE_PORT", "8000"))

DB_PATH = Path(os.getenv("DRONE_DB_PATH", str(BASE_DIR / "data" / "drone_sensor.db")))
ZONES_SEED_PATH = Path(
    os.getenv("DRONE_ZONES_SEED_PATH", str(BASE_DIR / "app" / "zones.seed.json"))
)

# SQLite by default (zero setup). Point this at a PostgreSQL instance for
# production deployments that need concurrent-write throughput SQLite can't
# offer, e.g. postgresql+psycopg2://user:pass@host:5432/dbname
DATABASE_URL = os.getenv("DRONE_DATABASE_URL", f"sqlite:///{DB_PATH}")

LOG_LEVEL = os.getenv("DRONE_LOG_LEVEL", "INFO")
# "text" (human-readable) or "json" (structured, one JSON object per line --
# suited to log aggregators like ELK/Loki/CloudWatch).
LOG_FORMAT = os.getenv("DRONE_LOG_FORMAT", "text")

# When unset (the default), the API requires no authentication -- fine as
# long as it's only bound to 127.0.0.1. Set this before exposing the app
# beyond localhost, and every request must then send a matching
# X-API-Key header.
#
# DRONE_API_KEY is a single legacy key granted the "admin" role (full
# access) for backwards compatibility. For real deployments prefer
# DRONE_API_KEYS: a JSON object mapping each key to a role, e.g.
#   DRONE_API_KEYS={"radar-1-key": "ingest", "ops-key": "operator"}
# Roles: "ingest" (POST detections only), "viewer" (read-only GETs),
# "operator" (viewer + acknowledge incidents), "admin" (everything,
# including managing sensor registrations and authorized operators).
API_KEY = os.getenv("DRONE_API_KEY", "")
API_KEYS_JSON = os.getenv("DRONE_API_KEYS", "")

# Requests per second a single sensor_id may submit detections at before
# POST /api/detections starts returning 429. Generous default -- this is a
# backstop against a malfunctioning or malicious sensor, not a normal-load
# limit.
RATE_LIMIT_PER_SECOND = float(os.getenv("DRONE_RATE_LIMIT_PER_SECOND", "50"))
RATE_LIMIT_BURST = float(os.getenv("DRONE_RATE_LIMIT_BURST", "100"))

# Detections older than this are purged by a periodic background task.
# 0 (or unset) disables purging -- keep everything forever, the previous
# behavior.
DETECTION_RETENTION_DAYS = float(os.getenv("DRONE_DETECTION_RETENTION_DAYS", "0"))
RETENTION_SWEEP_INTERVAL_SECONDS = float(
    os.getenv("DRONE_RETENTION_SWEEP_INTERVAL_SECONDS", "3600")
)

# Comma-separated URLs to POST a JSON payload to whenever an incident opens.
# Empty (default) disables outbound alerting entirely.
WEBHOOK_URLS = [u.strip() for u in os.getenv("DRONE_WEBHOOK_URLS", "").split(",") if u.strip()]
WEBHOOK_TIMEOUT_SECONDS = float(os.getenv("DRONE_WEBHOOK_TIMEOUT_SECONDS", "5"))

# Track association gates: a detection may only join a track if it arrives
# within TRACK_TIME_GATE_SECONDS of the track's last update and within
# TRACK_DISTANCE_GATE_M of its last known position.
TRACK_TIME_GATE_SECONDS = float(os.getenv("DRONE_TRACK_TIME_GATE_SECONDS", "30"))
TRACK_DISTANCE_GATE_M = float(os.getenv("DRONE_TRACK_DISTANCE_GATE_M", "500"))

# Track lifecycle: active -> lost after this many quiet seconds, lost -> closed
# after this many more.
TRACK_STALE_SECONDS = float(os.getenv("DRONE_TRACK_STALE_SECONDS", "30"))
TRACK_DROP_SECONDS = float(os.getenv("DRONE_TRACK_DROP_SECONDS", "300"))

# Classification thresholds.
DRONE_CONFIDENCE_THRESHOLD = float(os.getenv("DRONE_CONFIDENCE_THRESHOLD", "0.75"))
BIRD_CONFIDENCE_THRESHOLD = float(os.getenv("DRONE_BIRD_CONFIDENCE_THRESHOLD", "0.4"))

# Sensor health thresholds.
SENSOR_ONLINE_SECONDS = float(os.getenv("DRONE_SENSOR_ONLINE_SECONDS", "60"))
SENSOR_STALE_SECONDS = float(os.getenv("DRONE_SENSOR_STALE_SECONDS", "300"))

# IMM (Interacting Multiple Model) filter tuning -- see app/imm.py. Each
# process noise is the assumed variance (m^2/s^3) of an unmodeled
# acceleration between updates for that mode: CRUISE stays low so the
# filter smooths sensor jitter out on straight/level flight, MANEUVER
# stays high so a real turn or sudden acceleration gets explained by that
# mode rather than lagged behind. Measurement sigma is the assumed
# 1-sigma position error (m) of a detection at confidence 1.0; it's
# scaled up for lower-confidence detections so the filter trusts them less.
KALMAN_CRUISE_PROCESS_NOISE = float(os.getenv("DRONE_KALMAN_CRUISE_PROCESS_NOISE", "4.0"))
KALMAN_MANEUVER_PROCESS_NOISE = float(os.getenv("DRONE_KALMAN_MANEUVER_PROCESS_NOISE", "100.0"))
KALMAN_MEASUREMENT_SIGMA_M = float(os.getenv("DRONE_KALMAN_MEASUREMENT_SIGMA_M", "30.0"))
# A brand-new track has no velocity history, so its initial velocity
# uncertainty has to be loose enough to gate in a fast-moving object (e.g.
# a ~200+ m/s aircraft) at realistic, non-radar-fast update rates (ADS-B
# and radar can easily be ~1 Hz): with a too-tight prior, the filter's
# first predict step assumes near-zero velocity, so a fast mover's next
# real position lands outside the gate before the filter has had a second
# update to start learning its actual velocity. 100 m/s comfortably covers
# typical aircraft/drone speeds at 1 Hz with headroom to spare.
KALMAN_INITIAL_VELOCITY_SIGMA_MPS = float(
    os.getenv("DRONE_KALMAN_INITIAL_VELOCITY_SIGMA_MPS", "100.0")
)

# Gate for accepting a detection onto a track: squared Mahalanobis distance
# between the detection and the track's Kalman-predicted position must be
# below this. 9.21 is the chi-square 99% threshold for 2 degrees of freedom.
TRACK_GATE_CHI2 = float(os.getenv("DRONE_TRACK_GATE_CHI2", "9.21"))

# How far ahead (seconds) a track's velocity is projected to check for an
# upcoming restricted-zone entry that hasn't happened yet.
PREDICTIVE_HORIZON_SECONDS = float(os.getenv("DRONE_PREDICTIVE_HORIZON_SECONDS", "30"))

# How many of a track's most recent detections feed classification fusion
# (app/fusion.py). Bounds the cost of fusing a long-lived track's history
# on every new detection.
FUSION_HISTORY_LIMIT = int(os.getenv("DRONE_FUSION_HISTORY_LIMIT", "50"))
