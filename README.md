# Drone Multi-Sensor

Ingests detections from multiple sensor types, fuses them into tracks,
classifies each track, flags restricted-zone incursions as incidents, and
shows it all on a live dashboard.

## Setup

Windows (PowerShell):

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

macOS / Linux:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

## Run

Windows:

```powershell
.venv\Scripts\python.exe main.py
```

macOS / Linux:

```bash
.venv/bin/python main.py
```

This starts the API + dashboard at **http://127.0.0.1:8000** (auto-reload
enabled) and creates a SQLite database at `data/drone_sensor.db` on first
run. Open the URL in a browser for the dashboard, or `/docs` for interactive
API docs.

> **Windows note:** `--reload` spawns a separate child process to actually
> serve requests. If you stop the app with Ctrl+C and a later run seems to
> ignore code changes or return stale data, check for a leftover process
> holding port 8000 (`Get-NetTCPConnection -LocalPort 8000`) and kill it by
> PID before restarting.

To run without auto-reload (e.g. for testing):

```powershell
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

```bash
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

## Sending a detection

PowerShell (`curl` is aliased to `Invoke-WebRequest` there, which doesn't
take `-d`/`-H` the way you'd expect, so use `Invoke-RestMethod` instead):

```powershell
$body = @{ sensor_id = "radar-1"; sensor_type = "radar"; timestamp = "2026-08-09T12:00:00"; latitude = 51.5; longitude = -0.1; altitude_m = 90; confidence = 0.9 } | ConvertTo-Json
Invoke-RestMethod -Uri http://127.0.0.1:8000/api/detections -Method Post -ContentType "application/json" -Body $body
# If DRONE_API_KEY is set, add: -Headers @{ "X-API-Key" = "<your key>" }
```

Bash / macOS / Linux:

```bash
curl -X POST http://127.0.0.1:8000/api/detections \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <your key, if DRONE_API_KEY is set>" \
  -d '{"sensor_id":"radar-1","sensor_type":"radar","timestamp":"2026-08-09T12:00:00","latitude":51.5,"longitude":-0.1,"altitude_m":90,"confidence":0.9}'
```

The response includes the `track_id` the detection was associated with (a
new track is created if nothing matched within the time/distance gates).

## Simulator

`simulator.py` posts a stream of realistic detections against a running
server: a drone flying into the seeded restricted zone, an ADS-B aircraft
passing well clear of it, and a low-confidence camera return that reads as
a bird.

Windows:

```powershell
.venv\Scripts\python.exe simulator.py
.venv\Scripts\python.exe simulator.py --ticks 50 --interval 0.5 --seed 42
.venv\Scripts\python.exe simulator.py --api-key <your key>   # if DRONE_API_KEY is set on the server
```

macOS / Linux:

```bash
.venv/bin/python simulator.py
.venv/bin/python simulator.py --ticks 50 --interval 0.5 --seed 42
.venv/bin/python simulator.py --api-key <your key>   # if DRONE_API_KEY is set on the server
```

Watch the dashboard while it runs to see tracks appear, get classified, and
trigger a zone-incursion alert.

## Tests

```
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m pytest tests/
```

Each test runs against an isolated, temporary SQLite database (see
`tests/conftest.py`), so the suite never touches `data/drone_sensor.db`.
CI runs the same suite on every push/PR (`.github/workflows/tests.yml`).

To run the exact same suite against PostgreSQL instead (proves real
parity, not just "it imports"):

```bash
createdb drone_test
DRONE_TEST_DATABASE_URL="postgresql+psycopg2://user:pass@127.0.0.1:5432/drone_test" \
  .venv/bin/python -m pytest tests/
```

Each test drops and recreates the `public` schema on that database, so
point it at a disposable database, never one with real data.

## Docker

```
docker build -t drone-multi-sensor .
docker run -p 8000:8000 -v drone-data:/app/data drone-multi-sensor
```

The image binds to `0.0.0.0:8000` inside the container (so `docker run -p`
can reach it) and stores the SQLite database in `/app/data` — the `-v` above
keeps it across container restarts. Set `-e DRONE_API_KEY=<key>` if the
container's port will be reachable beyond your own machine.

## API

| Endpoint | Description |
|---|---|
| `GET /api/health` | Liveness check (unauthenticated) |
| `GET /api/metrics` | Prometheus metrics (unauthenticated) |
| `POST /api/detections` | Ingest a detection; runs georeferencing, Kalman track association, classification fusion, and zone-incident checks |
| `GET /api/tracks` / `GET /api/tracks/{id}` | List or fetch tracks (`?status=active\|lost\|closed`, `?limit=`, `?offset=`) |
| `GET /api/incidents` | List incidents (`?status=open\|acknowledged\|resolved`, `?limit=`, `?offset=`) |
| `POST /api/incidents/{id}/acknowledge` | Acknowledge an open incident |
| `POST /api/incidents/{id}/resolve` | Resolve an incident |
| `GET /api/zones` | List active zones |
| `GET /api/sensors` | Per-sensor health, derived from each sensor's most recent detection |
| `GET /api/sensor-registrations` | List registered sensors (position/orientation used for georeferencing) |
| `PUT /api/sensor-registrations/{sensor_id}` | Register/update a sensor's fixed position and orientation (admin) |
| `GET /api/authorized-operators` | List authorized ("friendly") drone operators (admin) |
| `PUT /api/authorized-operators/{operator_id}` | Register/update an authorized operator (admin) |

## Tracking core

Each track runs its own IMM (Interacting Multiple Model) filter
(`app/imm.py`) in a local flat-earth frame anchored at the track's first
fix (`app/geo.py`): a CRUISE mode (low process noise, smooths sensor
jitter on straight/level flight) and a MANEUVER mode (high process noise,
tracks sharp turns and sudden acceleration without lagging behind them),
blended by mode probabilities that update every detection from how well
each mode's prediction matched it. A plain single-model constant-velocity
filter treats a sharp turn as measurement noise and lags behind it; this
doesn't. Tracks in the API/dashboard carry `heading_deg`, `speed_mps`,
`position_uncertainty_m`, and `maneuver_probability` (0-1: how likely the
tracker thinks this object is currently maneuvering) derived from the
filter.

Every incoming detection is gated against each active track's IMM-predicted
position (not just its last raw fix) using squared Mahalanobis distance, so
a fast mover doesn't fall outside a gate sized for a hovering one, and a
stable track gates tighter than a maneuvering one.

`POST /api/detections` also projects each updated track's velocity
`DRONE_PREDICTIVE_HORIZON_SECONDS` ahead and opens a `predicted_incursion`
incident if the projection enters a restricted zone the track isn't
already inside — an early warning ahead of the actual `zone_incursion`.

Association is still per-detection greedy nearest-match (lowest
Mahalanobis distance within gate), not a batch global-nearest-neighbor or
full multi-hypothesis tracker (JPDA/MHT) -- those need detections buffered
into a batch and resolved together, which trades the API's immediate
per-request response for latency. Two tracks crossing paths very close
together can still swap identities as a result. This is a deliberate,
documented scope boundary, not an oversight.

## Database

SQLite by default -- nothing to configure, works out of the box, and is
genuinely fine for a single-process deployment. For production-scale
concurrent write throughput, point `DRONE_DATABASE_URL` at PostgreSQL
instead:

```bash
pip install -r requirements-postgres.txt
DRONE_DATABASE_URL="postgresql+psycopg2://user:pass@host:5432/dbname" .venv/bin/python main.py
```

The storage layer (`app/db.py`) is plain SQL via SQLAlchemy Core against a
schema defined once in `app/schema.py`, so both backends run the exact
same code path -- the full test suite passes unmodified against either
(see "Tests" below). Table/column additions since the initial release are
picked up on startup via a lightweight migration check; there's no
migration framework (Alembic etc.) since the schema is still small enough
to evolve by hand.

## Sensor realism

**Georeferencing** (`app/georeference.py`): a detection that reports
`azimuth_deg`/`range_m` (typical of a fixed radar or RF direction-finder)
instead of `latitude`/`longitude` is converted to an absolute position
using the sensor's registered mounting position and orientation. Register
a sensor first:

```bash
curl -X PUT http://127.0.0.1:8000/api/sensor-registrations/radar-1 \
  -H "Content-Type: application/json" \
  -d '{"sensor_type":"radar","latitude":51.50,"longitude":-0.10,"altitude_m":15,"azimuth_reference_deg":0}'
```

`azimuth_reference_deg` is the compass bearing the sensor's own
`azimuth_deg=0` points to (0 if it's already mounted true-north-referenced).
Detections that already carry lat/lon (GPS-tagged cameras, ADS-B, etc.)
skip this entirely.

**ADS-B via dump1090** (`app/adapters/`): the most common real-world way
to get ADS-B into this tracker is an RTL-SDR dongle (~$20) running
[dump1090](https://github.com/flightaware/dump1090) or similar, which
serves live traffic on TCP port 30003 in the SBS-1/BaseStation text
format. A small bridge script parses that feed and posts it to the API:

```bash
.venv/bin/python -m app.adapters.dump1090_bridge --sbs-host 127.0.0.1 --sbs-port 30003
```

**Camera motion cueing** (`app/adapters/camera_motion.py`): watches a
webcam or RTSP camera stream with OpenCV background subtraction and posts
a `camera` detection whenever it sees motion above a threshold.

```bash
pip install -r requirements-camera.txt
.venv/bin/python -m app.adapters.camera_motion --source rtsp://192.168.1.50/stream1 \
  --target-lat 51.50 --target-lon -0.10 --confidence 0.5
```

**This is a motion detector, not an object classifier** -- it can't tell a
drone from a bird, a cat, or a swaying branch, and (being a single
monocular camera) can't measure range, so every detection is reported at
a fixed `--target-lat`/`--target-lon` representing the center of the
camera's field of view rather than a triangulated position. The
`--confidence` value is a manual estimate you tune to your own scene (a
camera that only ever sees open sky can reasonably use a higher value
than one that also sees traffic or trees), not something derived from
what's actually in frame. A real deployment would replace this with a
trained object detector (YOLO or similar) scoring actual object class.

## Classification fusion & friendly allowlist

Track classification (`app/fusion.py`) is a confidence-and-sensor-trust
weighted vote across a track's recent detections, not just its single
latest one: each detection votes for its own label, weighted by
`sensor trust × confidence` (ADS-B trusted highest, acoustic lowest, see
`SENSOR_TRUST` in `app/fusion.py`). One noisy low-trust reading can no
longer flip an established track's classification on its own -- it has to
outweigh the accumulated evidence. A track can always be *upgraded* to
`drone` from a lower-confidence label (never silently downgraded away from
one), since misclassifying a real drone as a bird and never re-flagging it
is the unsafe failure mode.

A detection whose `raw_data.operator_id` matches a registered authorized
operator (`app/allowlist.py` -- meant for FAA Remote ID broadcasts) votes
`friendly` instead. This is **not cryptographically verified** -- Remote ID
broadcasts aren't signed, so a resourced adversary could spoof an
authorized `operator_id`. Two things limit the damage a spoofed claim can
do: `GET /api/authorized-operators` is **admin-only** (not `viewer`), since
it's exactly the list of values needed to spoof the check, and a
`friendly` classification tempers an incident's severity to `medium`
rather than suppressing it to `low` the way independently-verified
evidence (ADS-B, `aircraft`) does -- an unverified self-report shouldn't be
able to fully silence a real intrusion. Register an operator:

```bash
curl -X PUT http://127.0.0.1:8000/api/authorized-operators/OP-12345 \
  -H "Content-Type: application/json" -H "X-API-Key: <admin key>" \
  -d '{"name":"Acme Surveying Co."}'
```

## Operations

- **Rate limiting**: `POST /api/detections` is limited per `sensor_id` by
  an in-memory token bucket (`DRONE_RATE_LIMIT_PER_SECOND`/`_BURST`) --
  a backstop against a malfunctioning or malicious sensor, not a normal-load
  limit. Returns 429 when exceeded.
- **Retention**: set `DRONE_DETECTION_RETENTION_DAYS` to periodically purge
  detections older than that many days (a background task sweeps every
  `DRONE_RETENTION_SWEEP_INTERVAL_SECONDS`, disabled by default -- keeps
  everything forever, the previous behavior).
- **Structured logging**: `DRONE_LOG_FORMAT=json` emits one JSON object per
  log line instead of human-readable text, for log aggregators.
- **Metrics**: `GET /api/metrics` in Prometheus exposition format --
  detections ingested (by sensor type), incidents opened (by type/severity),
  rate-limit rejections, and live gauges for active tracks / open incidents.
- **Outbound alerting**: set `DRONE_WEBHOOK_URLS` (comma-separated) to POST
  each incident's JSON to one or more webhooks when it opens.

## Configuration

All settings are environment variables with working defaults — nothing
needs to be set to run locally.

| Variable | Default | Purpose |
|---|---|---|
| `DRONE_HOST` | `127.0.0.1` | Bind host |
| `DRONE_PORT` | `8000` | Bind port |
| `DRONE_DB_PATH` | `data/drone_sensor.db` | SQLite file location (used to build the default `DRONE_DATABASE_URL`) |
| `DRONE_DATABASE_URL` | `sqlite:///<DRONE_DB_PATH>` | SQLAlchemy database URL; point at PostgreSQL for production |
| `DRONE_ZONES_SEED_PATH` | `app/zones.seed.json` | Zone seed file, loaded at startup |
| `DRONE_LOG_LEVEL` | `INFO` | Logging level |
| `DRONE_LOG_FORMAT` | `text` | `text` or `json` (structured, one object per line) |
| `DRONE_API_KEY` | *(unset)* | Legacy single key, granted the `admin` role. Prefer `DRONE_API_KEYS` for real deployments |
| `DRONE_API_KEYS` | *(unset)* | JSON object mapping each key to a role: `ingest`, `viewer`, `operator`, or `admin` |
| `DRONE_RATE_LIMIT_PER_SECOND` | `50` | Per-sensor detection ingest rate limit |
| `DRONE_RATE_LIMIT_BURST` | `100` | Per-sensor token-bucket burst capacity |
| `DRONE_DETECTION_RETENTION_DAYS` | `0` (disabled) | Purge detections older than this many days |
| `DRONE_RETENTION_SWEEP_INTERVAL_SECONDS` | `3600` | How often the retention purge runs |
| `DRONE_WEBHOOK_URLS` | *(unset)* | Comma-separated URLs POSTed with each incident's JSON when it opens |
| `DRONE_WEBHOOK_TIMEOUT_SECONDS` | `5` | Per-webhook request timeout |
| `DRONE_FUSION_HISTORY_LIMIT` | `50` | Max recent detections per track fed into classification fusion |
| `DRONE_TRACK_TIME_GATE_SECONDS` | `30` | Max age gap for a detection to join a track |
| `DRONE_TRACK_DISTANCE_GATE_M` | `500` | Max distance for a detection to join a track |
| `DRONE_TRACK_STALE_SECONDS` | `30` | Active track goes `lost` after this many quiet seconds |
| `DRONE_TRACK_DROP_SECONDS` | `300` | Lost track goes `closed` after this many more |
| `DRONE_CONFIDENCE_THRESHOLD` | `0.75` | Confidence at/above which a detection is classified `drone` |
| `DRONE_BIRD_CONFIDENCE_THRESHOLD` | `0.4` | Below this, a camera/acoustic detection is classified `bird` |
| `DRONE_SENSOR_ONLINE_SECONDS` | `60` | Sensor shows `online` if seen within this window |
| `DRONE_SENSOR_STALE_SECONDS` | `300` | Sensor shows `stale` up to this window, `offline` beyond it |
| `DRONE_KALMAN_CRUISE_PROCESS_NOISE` | `4.0` | IMM CRUISE mode's assumed unmodeled-acceleration variance (m²/s³) -- low, smooths sensor jitter on straight/level flight |
| `DRONE_KALMAN_MANEUVER_PROCESS_NOISE` | `100.0` | IMM MANEUVER mode's assumed unmodeled-acceleration variance (m²/s³) -- high, tracks sharp turns without lagging |
| `DRONE_KALMAN_MEASUREMENT_SIGMA_M` | `30.0` | Assumed 1-sigma position error (m) of a detection at confidence 1.0; scaled up for lower confidence |
| `DRONE_KALMAN_INITIAL_VELOCITY_SIGMA_MPS` | `100.0` | Initial velocity uncertainty (1-sigma, m/s) for a brand-new track |
| `DRONE_TRACK_GATE_CHI2` | `9.21` | Squared-Mahalanobis-distance gate for a detection to join a track (99% chi-square, 2 DOF) |
| `DRONE_PREDICTIVE_HORIZON_SECONDS` | `30` | How far ahead a track's velocity is projected to raise an early zone-incursion warning |

## Security & access control

By default the app binds to `127.0.0.1` and requires no authentication —
fine as-is, since nothing outside this machine can reach it. If you ever
want to reach it from another device (phone, another PC on your LAN),
**set an API key before changing `DRONE_HOST`**.

For a single trusted key with full access:

```bash
DRONE_API_KEY="some long random string" .venv/bin/python main.py
```

For real deployments, prefer per-key roles via `DRONE_API_KEYS` -- a JSON
object mapping each key to a role:

```bash
export DRONE_API_KEYS='{"radar-1-key":"ingest","ops-key":"operator","admin-key":"admin"}'
```

| Role | Can do |
|---|---|
| `ingest` | `POST /api/detections` only -- what a sensor's own key should get |
| `viewer` | Read tracks/incidents/zones/sensors, nothing else |
| `operator` | `viewer` + acknowledge/resolve incidents |
| `admin` | Everything, including registering sensors and authorized operators |

An `ingest`-scoped key deliberately can't read tracks or incidents back --
a compromised sensor credential shouldn't be able to see what the system
knows. Every `/api/*` request other than `/api/health` and `/api/metrics`
needs a matching `X-API-Key` header once any key is configured, or it gets
a 401 (403 if the key is valid but its role doesn't cover that endpoint).
The dashboard prompts for a key inline on first 401 and remembers it in the
browser's `localStorage`; the simulator picks one up from `--api-key` or
`$DRONE_API_KEY`.

Without any key configured, don't expose the port beyond localhost —
anyone who can reach it could inject fake detections or acknowledge
(silence) real alerts.

## Project layout

```
app/
  main.py               FastAPI app, routes, RBAC wiring, retention sweep, dashboard
  models.py             Pydantic domain models
  db.py                 SQLAlchemy Core storage helpers (SQLite + PostgreSQL)
  schema.py             SQLAlchemy table definitions (the schema, portable across backends)
  config.py             Environment-variable settings
  logging_config.py     Logging setup (text or JSON)
  auth.py               API key + role-based access control (RBAC)
  ratelimit.py          Per-sensor token-bucket rate limiter
  metrics.py             Prometheus counters
  notifications.py       Outbound webhook alerting on incident open
  classification.py     Single-detection sensor + confidence -> label rule
  fusion.py              Multi-sensor classification fusion across a track's detections
  allowlist.py           Friendly-operator allowlist (authorized_operator table)
  georeference.py         Sensor-relative azimuth/range -> absolute lat/lon
  tracking.py            Track association (Kalman-gated), update, expiry
  kalman.py               Constant-velocity Kalman filter (no numpy dependency)
  imm.py                  IMM (Interacting Multiple Model) filter: blends CRUISE + MANEUVER modes
  geo.py                  Great-circle distance, local tangent-plane projection, forward geodesic
  incidents.py            Zone-incursion + predicted-incursion incident creation
  zones.py               Zone loading + point-in-polygon test
  sensors.py              Sensor health
  util.py                 Shared helpers (naive-UTC now())
  zones.seed.json        Sample restricted zone
  adapters/               Real-sensor bridges (SBS-1/dump1090 ADS-B, OpenCV camera motion cueing)
  api/                    Route handlers, one module per resource
  static/dashboard.html   Dashboard (no build step; loads Leaflet + map tiles from a CDN, so it needs internet access)
tests/                     Pytest suite (runs against SQLite by default, PostgreSQL optionally)
simulator.py               Posts realistic detections against a running server
main.py                    Entrypoint (python main.py)
Dockerfile                 Single-stage container build
```
