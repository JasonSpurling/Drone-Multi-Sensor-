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
