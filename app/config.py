"""Centralized configuration, sourced from environment variables with
sensible defaults so the app runs out of the box with no setup.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# file_path -> (mtime_ns at last read, stripped content). _read_secret() is
# called from get_api_key()/get_api_keys_json(), which app.auth.
# configured_keys() calls on every authenticated request and WebSocket
# connect -- an os.stat() per call is far cheaper than a full read()+strip()
# of the file every time, while still picking up a rotation on the very
# next request after the file's mtime actually changes (no restart, no
# polling interval to wait out).
_secret_file_cache: dict[str, tuple[int, str]] = {}


def _read_secret(env_var: str, default: str = "") -> str:
    """Reads a secret from `{env_var}_FILE` (that file's content,
    whitespace-stripped) if set, else from the plain `{env_var}` env var,
    else `default` -- the same `_FILE`-suffix convention Docker/Kubernetes
    secrets use (e.g. `POSTGRES_PASSWORD_FILE` in the official postgres
    image), safer than a raw env var: an env var is visible to any process
    that can read `/proc/<pid>/environ` or run `docker inspect` on the
    container, while a secret mounted from a real secret store as a file
    can be permissioned/audited independently of the process's own
    environment.
    """
    file_path = os.getenv(f"{env_var}_FILE")
    if file_path:
        try:
            mtime_ns = os.stat(file_path).st_mtime_ns
        except FileNotFoundError as exc:
            raise RuntimeError(
                f"{env_var}_FILE={file_path!r} is set but that file doesn't "
                "exist (secret volume not mounted yet? typo'd path?)"
            ) from exc
        cached = _secret_file_cache.get(file_path)
        if cached is not None and cached[0] == mtime_ns:
            return cached[1]
        content = Path(file_path).read_text().strip()
        _secret_file_cache[file_path] = (mtime_ns, content)
        return content
    return os.getenv(env_var, default)


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
#
# Kept as plain module-level assignments (like everything else here, and
# still what the test suite's `monkeypatch.setattr("app.config.API_KEY",
# ...)` throughout patches) -- get_api_key()/get_api_keys_json() below are
# the file-aware, rotation-capable accessors app.auth.configured_keys()
# actually calls; these two stay as the plain-env-var fallback they read.
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

# A second, site-wide backstop on top of the per-sensor one above: many
# distinct sensor_ids (real ones, or an attacker minting new ones to dodge
# the per-sensor bucket -- nothing currently stops an ingest-role key from
# claiming any sensor_id) can each stay individually within
# RATE_LIMIT_PER_SECOND while collectively overwhelming this site's share
# of the server. Scoped per-site (not one deployment-wide bucket) so one
# site's load can't starve another's -- consistent with every other
# resource in this app being site-scoped. Generous default relative to the
# per-sensor limit -- sized for "many well-behaved sensors at once", not a
# normal single-sensor load.
GLOBAL_RATE_LIMIT_PER_SECOND = float(os.getenv("DRONE_GLOBAL_RATE_LIMIT_PER_SECOND", "500"))
GLOBAL_RATE_LIMIT_BURST = float(os.getenv("DRONE_GLOBAL_RATE_LIMIT_BURST", "1000"))

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

# Finished (non-active) tracks older than this are purged alongside
# detections -- 0 (default) disables it. A separate knob from detection
# retention: a deployment might want to keep track summaries much longer
# than raw per-detection data, or vice versa.
TRACK_RETENTION_DAYS = float(os.getenv("DRONE_TRACK_RETENTION_DAYS", "0"))

# Audit log entries older than this are purged -- 0 (default) disables it,
# keeping the audit trail forever. Deliberately independent of the other
# two: compliance requirements for "who did what" commonly outlive both
# raw sensor data and track history.
AUDIT_LOG_RETENTION_DAYS = float(os.getenv("DRONE_AUDIT_LOG_RETENTION_DAYS", "0"))

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

SLACK_WEBHOOK_URL = _read_secret("DRONE_SLACK_WEBHOOK_URL")
SLACK_MIN_SEVERITY = os.getenv("DRONE_SLACK_MIN_SEVERITY", "low")

PAGERDUTY_ROUTING_KEY = _read_secret("DRONE_PAGERDUTY_ROUTING_KEY")
PAGERDUTY_MIN_SEVERITY = os.getenv("DRONE_PAGERDUTY_MIN_SEVERITY", "high")

# SMS via Twilio's REST API.
TWILIO_ACCOUNT_SID = _read_secret("DRONE_TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = _read_secret("DRONE_TWILIO_AUTH_TOKEN")
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
MITIGATION_WEBHOOK_URL = _read_secret("DRONE_MITIGATION_WEBHOOK_URL")
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
FAA_NOTAM_CLIENT_SECRET = _read_secret("DRONE_FAA_NOTAM_CLIENT_SECRET")

# Generic OpenID Connect SSO login (app/oidc.py, app/api/auth_sso.py) --
# an alternative to DRONE_API_KEY(S) for a human operator logging into the
# dashboard through a browser, not a replacement for it (a sensor's own
# ingest key still authenticates the same way). Works with any standards-
# compliant OIDC provider (Okta, Auth0, Azure AD, Google Workspace, a
# self-hosted Keycloak, ...) via issuer discovery -- no vendor-specific
# code. The feature is only active once all three of ISSUER_URL/CLIENT_ID/
# CLIENT_SECRET are set; see app.oidc.oidc_enabled().
OIDC_ISSUER_URL = os.getenv("DRONE_OIDC_ISSUER_URL", "")
OIDC_CLIENT_ID = os.getenv("DRONE_OIDC_CLIENT_ID", "")
OIDC_CLIENT_SECRET = _read_secret("DRONE_OIDC_CLIENT_SECRET")
# Where the IdP redirects back to after login -- defaults to this app's
# own /auth/callback route computed from the incoming request, which is
# fine for a single-hostname deployment; set explicitly if this app sits
# behind a reverse proxy/load balancer that rewrites the host the app
# itself sees (so the IdP-registered redirect URI matches what's actually
# public).
OIDC_REDIRECT_URL = os.getenv("DRONE_OIDC_REDIRECT_URL", "")

# Which ID token claims map to this app's own role/site model (see
# app/auth.py's Principal) -- configurable since every IdP's claim
# naming differs (a custom claim, a group name, ...); this app doesn't
# assume one. A claim value that isn't one of ingest/viewer/operator/admin
# falls back to OIDC_DEFAULT_ROLE (logged as a warning, never silently
# escalated to something more privileged than that default).
OIDC_ROLE_CLAIM = os.getenv("DRONE_OIDC_ROLE_CLAIM", "role")
OIDC_SITE_CLAIM = os.getenv("DRONE_OIDC_SITE_CLAIM", "site")
OIDC_DEFAULT_ROLE = os.getenv("DRONE_OIDC_DEFAULT_ROLE", "viewer")

# Symmetric key sealing the session cookie app/oidc.py's callback route
# issues after a successful login (see app/sso_session.py) -- also reused
# as Starlette SessionMiddleware's own signing secret for the short-lived
# state/nonce cookie the OIDC flow needs mid-redirect (a different,
# transient cookie, never carries role/site data). Must be a Fernet key:
# generate one with
#   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# No default -- unlike every other secret in this file, a blank/predictable
# session-signing key would let anyone forge an admin session, so this
# fails closed (app.main refuses to start with OIDC enabled but this
# unset) rather than falling back to something guessable.
OIDC_SESSION_SECRET = _read_secret("DRONE_OIDC_SESSION_SECRET")
OIDC_SESSION_MAX_AGE_SECONDS = float(os.getenv("DRONE_OIDC_SESSION_MAX_AGE_SECONDS", "28800"))  # 8h

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


def get_api_key() -> str:
    """The file-aware, rotation-capable accessor for the legacy single
    admin key -- app.auth.configured_keys() calls this (not the plain
    API_KEY attribute above) so a key rotation takes effect on the very
    next request, no restart needed.

    A plain module attribute can't do this: an earlier version of this
    file tried making API_KEY itself dynamic via a module __getattr__
    (PEP 562), but that broke the moment any test did
    `monkeypatch.setattr("app.config.API_KEY", ...)` -- monkeypatch saves
    the "old value" by calling getattr() *before* patching (which, for an
    attribute that only existed via __getattr__, resolves and caches a
    concrete string), then restores that concrete value via a real
    setattr() at teardown instead of deleting it -- permanently shadowing
    __getattr__ for every test that ran afterward in the same process.
    Two separate names (a plain attribute tests can keep monkeypatching,
    and this function for the dynamic/file-aware path) avoids that
    entirely.

    Falls back to the plain API_KEY attribute (so a test's monkeypatch of
    it still works, since that's an ordinary module-global lookup at call
    time) when DRONE_API_KEY_FILE isn't set.
    """
    return _read_secret("DRONE_API_KEY", default=API_KEY)


def get_api_keys_json() -> str:
    """Same as get_api_key(), for DRONE_API_KEYS/DRONE_API_KEYS_FILE."""
    return _read_secret("DRONE_API_KEYS", default=API_KEYS_JSON)
