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

LOG_LEVEL = os.getenv("DRONE_LOG_LEVEL", "INFO")

# When unset (the default), the API requires no authentication -- fine as
# long as it's only bound to 127.0.0.1. Set this before exposing the app
# beyond localhost, and every request must then send a matching
# X-API-Key header.
API_KEY = os.getenv("DRONE_API_KEY", "")

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

# Kalman filter tuning. Process noise is the assumed variance (m^2/s^3) of
# an unmodeled acceleration between updates -- higher values let the filter
# trust new detections more and follow maneuvers faster, at the cost of
# smoothing less noise out. Measurement sigma is the assumed 1-sigma
# position error (m) of a detection at confidence 1.0; it's scaled up for
# lower-confidence detections so the filter trusts them less.
KALMAN_PROCESS_NOISE = float(os.getenv("DRONE_KALMAN_PROCESS_NOISE", "4.0"))
KALMAN_MEASUREMENT_SIGMA_M = float(os.getenv("DRONE_KALMAN_MEASUREMENT_SIGMA_M", "30.0"))
KALMAN_INITIAL_VELOCITY_SIGMA_MPS = float(
    os.getenv("DRONE_KALMAN_INITIAL_VELOCITY_SIGMA_MPS", "40.0")
)

# Gate for accepting a detection onto a track: squared Mahalanobis distance
# between the detection and the track's Kalman-predicted position must be
# below this. 9.21 is the chi-square 99% threshold for 2 degrees of freedom.
TRACK_GATE_CHI2 = float(os.getenv("DRONE_TRACK_GATE_CHI2", "9.21"))

# How far ahead (seconds) a track's velocity is projected to check for an
# upcoming restricted-zone entry that hasn't happened yet.
PREDICTIVE_HORIZON_SECONDS = float(os.getenv("DRONE_PREDICTIVE_HORIZON_SECONDS", "30"))
