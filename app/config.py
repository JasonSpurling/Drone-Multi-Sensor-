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

# Unset by default -- unlike ZONES_SEED_PATH, this app ships no bundled file
# here, because there is no real per-model RF signature data to bundle (see
# app/rf_signatures.py's module docstring). Point this at a JSON file of
# your own operator-supplied signatures (real captures, a licensed RF
# signature library, or verified FCC equipment-authorization filings) to
# get real per-model fidelity; this app never fabricates that data itself.
RF_SIGNATURES_PATH = Path(os.environ["DRONE_RF_SIGNATURES_PATH"]) if os.getenv("DRONE_RF_SIGNATURES_PATH") else None

# SQLite by default (zero setup). Point this at a PostgreSQL instance for
# production deployments that need concurrent-write throughput SQLite can't
# offer, e.g. postgresql+psycopg2://user:pass@host:5432/dbname
DATABASE_URL = os.getenv("DRONE_DATABASE_URL", f"sqlite:///{DB_PATH}")

# PostgreSQL connection pool sizing (ignored for SQLite, which doesn't use
# SQLAlchemy's QueuePool). Detection ingest is fully serialized through
# app.tracking's process-wide _association_lock regardless of pool size, so
# these defaults (SQLAlchemy's own) are plenty for that path; what they
# actually bound is read-heavy endpoints like GET /api/tracks, which every
# dashboard viewer polls every few seconds -- exposed as env vars so a
# deployment with many concurrent dashboard viewers can size the pool to
# its real concurrency instead of being stuck with a guess baked into code.
DB_POOL_SIZE = int(os.getenv("DRONE_DB_POOL_SIZE", "5"))
DB_MAX_OVERFLOW = int(os.getenv("DRONE_DB_MAX_OVERFLOW", "10"))

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

# Comma-separated origins allowed to make cross-origin browser requests
# (e.g. https://ops.example.com) -- empty (default) means no CORS headers
# are sent at all, so a browser blocks any cross-origin JS from reading a
# response, same as before this existed. Only needed if you're hosting the
# dashboard (or a different frontend) on a different origin than this API;
# same-origin use (the bundled dashboard.html served from this app, or a
# server-to-server integration) never needs this set. "*" allows any origin
# -- fine for a public read-only integration, never combine with a
# deployment that also has authentication disabled.
CORS_ORIGINS = [o.strip() for o in os.getenv("DRONE_CORS_ORIGINS", "").split(",") if o.strip()]

# Requests per second a single sensor_id may submit detections at before
# POST /api/detections starts returning 429. Generous default -- this is a
# backstop against a malfunctioning or malicious sensor, not a normal-load
# limit.
RATE_LIMIT_PER_SECOND = float(os.getenv("DRONE_RATE_LIMIT_PER_SECOND", "50"))
RATE_LIMIT_BURST = float(os.getenv("DRONE_RATE_LIMIT_BURST", "100"))

# POST /api/detections/batch's cost isn't linear in the number of plots: its
# Hungarian assignment builds an n x active-tracks cost matrix and solves it
# in worse-than-linear time (see the "Load testing" README section), so an
# unbounded batch size is a real (if low-severity) resource-exhaustion
# shape. Generous default -- a real radar sweep is nowhere near this size;
# this exists to reject a pathological request, not constrain normal use.
MAX_BATCH_SIZE = int(os.getenv("DRONE_MAX_BATCH_SIZE", "500"))

# A detection whose (client-supplied) timestamp is further than this from
# the server's own clock, in either direction, is rejected rather than fed
# into tracking -- a real risk in a multi-sensor deployment where each
# sensor keeps its own clock (not every cheap radar/RF rig is NTP-synced).
# An undetected skew doesn't just misdraw a timestamp: app.tracking's
# Kalman predict step uses the gap between a detection's timestamp and the
# track's last update as its dt, so a sensor whose clock has drifted far
# ahead would inflate that dt hugely, ballooning the predicted position's
# uncertainty (and, at the extreme, whatever numerical issues an
# arbitrarily large dt causes in the filter's process-noise math) on every
# detection from that sensor. Generous default -- real-world sensors can
# legitimately lag by several seconds under load without being "broken".
MAX_DETECTION_CLOCK_SKEW_SECONDS = float(os.getenv("DRONE_MAX_DETECTION_CLOCK_SKEW_SECONDS", "300"))

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

# Severity-routed alert integrations (app/alerting.py) -- on top of the
# generic webhook fan-out above. Each channel is disabled until its
# URL/credentials are set, and each has its own minimum-severity
# threshold ("low"/"medium"/"high"/"critical") below which it's skipped --
# an escalation policy, so e.g. Slack can get every incident while
# PagerDuty only pages for high+ and SMS is reserved for critical.
ALERT_TIMEOUT_SECONDS = float(os.getenv("DRONE_ALERT_TIMEOUT_SECONDS", "5"))

SLACK_WEBHOOK_URL = os.getenv("DRONE_SLACK_WEBHOOK_URL", "")
SLACK_MIN_SEVERITY = os.getenv("DRONE_SLACK_MIN_SEVERITY", "low")

PAGERDUTY_ROUTING_KEY = os.getenv("DRONE_PAGERDUTY_ROUTING_KEY", "")
PAGERDUTY_MIN_SEVERITY = os.getenv("DRONE_PAGERDUTY_MIN_SEVERITY", "high")

# SMS via Twilio's REST API.
TWILIO_ACCOUNT_SID = os.getenv("DRONE_TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("DRONE_TWILIO_AUTH_TOKEN", "")
TWILIO_FROM_NUMBER = os.getenv("DRONE_TWILIO_FROM_NUMBER", "")
SMS_TO_NUMBERS = [n.strip() for n in os.getenv("DRONE_SMS_TO_NUMBERS", "").split(",") if n.strip()]
SMS_MIN_SEVERITY = os.getenv("DRONE_SMS_MIN_SEVERITY", "critical")

# Mitigation system hook (app/mitigation.py): a generic webhook POST to a
# downstream counter-UAS system (RF jammer, net gun, interdiction
# platform, ...) carrying the track's current position/classification
# alongside the incident, for that system to act on. This app doesn't own
# or control any mitigation hardware -- it's a notifier, the same role
# app/alerting.py plays for human-facing channels, just aimed at an
# automated system instead of a person. Defaults to "high" (not "low" like
# Slack) since an automated mitigation action has real-world consequences
# a chat notification doesn't -- a deployment should opt into a lower
# threshold deliberately, not by inheriting a permissive default.
MITIGATION_WEBHOOK_URL = os.getenv("DRONE_MITIGATION_WEBHOOK_URL", "")
MITIGATION_MIN_SEVERITY = os.getenv("DRONE_MITIGATION_MIN_SEVERITY", "high")

# Optional message-queue fan-out (app/queue_publisher.py): best-effort NATS
# core PUB of every ingested detection and opened incident, alongside (not
# instead of) the normal synchronous single-process path. Empty (default)
# disables it entirely -- this is scaffolding for a future multi-site/
# high-throughput deployment to build a consumer on top of, not a redesign
# of ingest/fusion itself, which stays synchronous and in-process either way.
NATS_URL = os.getenv("DRONE_NATS_URL", "")
NATS_CONNECT_TIMEOUT_SECONDS = float(os.getenv("DRONE_NATS_CONNECT_TIMEOUT_SECONDS", "2"))
NATS_DETECTION_SUBJECT = os.getenv("DRONE_NATS_DETECTION_SUBJECT", "drone.detections")
NATS_INCIDENT_SUBJECT = os.getenv("DRONE_NATS_INCIDENT_SUBJECT", "drone.incidents")

# Optional Cursor on Target (CoT) fan-out (app/cot_publisher.py): sends a
# CoT event over UDP for every track update to a TAK Server or any
# CoT-consuming client (e.g. ATAK's own UDP CoT input), feeding this
# tracker's output into a broader TAK common-operating-picture. Unset
# (default) disables it entirely. Port 6969 is a commonly used ATAK UDP
# CoT default in public examples, not a single IANA-assigned standard --
# verify against your own TAK endpoint's actual configured input.
COT_UDP_HOST = os.getenv("DRONE_COT_UDP_HOST", "")
COT_UDP_PORT = int(os.getenv("DRONE_COT_UDP_PORT", "6969"))
COT_STALE_SECONDS = float(os.getenv("DRONE_COT_STALE_SECONDS", "60"))

# FAA NOTAM API credentials (app/airspace/faa_notam.py), free but requires
# registration at api.faa.gov -- unset (default) means the NOTAM CLI needs
# them passed explicitly instead. See that module's docstring for the
# caveat that this client wasn't validated against a live account.
FAA_NOTAM_CLIENT_ID = os.getenv("DRONE_FAA_NOTAM_CLIENT_ID", "")
FAA_NOTAM_CLIENT_SECRET = os.getenv("DRONE_FAA_NOTAM_CLIENT_SECRET", "")

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

# Behavioral pattern-of-life analysis (app/behavior.py) -- loitering is
# checked per-track on every update; formation/shadowing compare multiple
# active tracks against each other on a periodic background sweep (see
# BEHAVIOR_SWEEP_INTERVAL_SECONDS) since that's a different computational
# shape than a per-detection check.
LOITERING_RADIUS_M = float(os.getenv("DRONE_LOITERING_RADIUS_M", "75"))
LOITERING_MIN_DURATION_S = float(os.getenv("DRONE_LOITERING_MIN_DURATION_S", "120"))
SHADOWING_MAX_DISTANCE_M = float(os.getenv("DRONE_SHADOWING_MAX_DISTANCE_M", "30"))
SHADOWING_MIN_DURATION_S = float(os.getenv("DRONE_SHADOWING_MIN_DURATION_S", "60"))
FORMATION_MAX_SPACING_M = float(os.getenv("DRONE_FORMATION_MAX_SPACING_M", "100"))
FORMATION_HEADING_TOLERANCE_DEG = float(os.getenv("DRONE_FORMATION_HEADING_TOLERANCE_DEG", "15"))
FORMATION_SPEED_TOLERANCE_MPS = float(os.getenv("DRONE_FORMATION_SPEED_TOLERANCE_MPS", "2"))
BEHAVIOR_SWEEP_INTERVAL_SECONDS = float(os.getenv("DRONE_BEHAVIOR_SWEEP_INTERVAL_SECONDS", "30"))

# How many of a track's most recent detections feed classification fusion
# (app/fusion.py). Bounds the cost of fusing a long-lived track's history
# on every new detection.
FUSION_HISTORY_LIMIT = int(os.getenv("DRONE_FUSION_HISTORY_LIMIT", "50"))
