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

## Deployment

**Quick single-container run** (SQLite, fine for trying it out):

```bash
docker build -t drone-multi-sensor .
docker run -p 8000:8000 -v drone-data:/app/data drone-multi-sensor
```

The image binds to `0.0.0.0:8000` inside the container (so `docker run -p`
can reach it) and stores the SQLite database in `/app/data` — the `-v` above
keeps it across container restarts. Set `-e DRONE_API_KEY=<key>` if the
container's port will be reachable beyond your own machine. The image
installs `requirements-postgres.txt` (not just `requirements.txt`), runs as
an unprivileged user, and declares a `HEALTHCHECK` against `/api/health`
(below) -- Docker/Kubernetes can use it to detect and restart/route around
a container whose database has gone unreachable, not just one whose
process has crashed outright.

**Full stack with PostgreSQL** (production-shaped -- see the Database
section above for why SQLite alone doesn't hold up under concurrent
sensor ingest at real deployment throughput):

```bash
cp .env.example .env   # edit DRONE_API_KEYS at minimum
docker compose up -d
curl http://localhost:8000/api/health
```

`docker-compose.yml` runs the app against a real PostgreSQL container, with
the app waiting on Postgres's own healthcheck before it starts (no
"connection refused, DB not ready yet" race on first `up`). It also has a
commented-out Caddy service for automatic HTTPS (a free, auto-renewing
Let's Encrypt certificate) -- uncomment it and set `DRONE_DOMAIN` once you
have a real domain pointed at the host; this is the difference between a
private-network deployment and one safe to expose on the public internet.

**Bare-metal/VM, no Docker**: `deploy/drone-multi-sensor.service` is a
systemd unit (with `deploy/drone-multi-sensor.env.example` for its
environment file) that runs the app under an unprivileged system user with
`systemd`'s own sandboxing (`ProtectSystem=strict`, `NoNewPrivileges`,
...) and restarts it on failure. Logs go to stdout either way (`journalctl
-u drone-multi-sensor` under systemd, whatever log driver you configure
under Docker) -- `DRONE_LOG_FORMAT=json` gives structured lines for either
to hand to a log aggregator, and there's nothing this app needs to do
itself for rotation/retention of its own log file, since it doesn't write
one.

**Readiness check**: `GET /api/health` verifies the database is actually
reachable (a real `SELECT 1`, not just "the process is up"), returning 503
if it isn't -- point a load balancer's or orchestrator's health check at
this, not just a raw TCP/process check, or a container can look healthy
while every real request would fail.

None of this replaces the "what's missing to run this on real hardware"
gaps (sensor/RF front-end integration, TLS being opt-in rather than
default, time sync across physical sensors, ...) -- it's specifically
about running the software itself reliably once you do have real sensors
feeding it.

## API

| Endpoint | Description |
|---|---|
| `GET /api/health` | Liveness check (unauthenticated) |
| `GET /api/metrics` | Prometheus metrics (unauthenticated) |
| `POST /api/detections` | Ingest one detection; runs georeferencing, IMM track association, classification fusion, and zone-incident checks |
| `POST /api/detections/batch` | Ingest simultaneous detections (e.g. one radar scan's plots), resolved jointly via global nearest neighbor |
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

`POST /api/detections` (single) is greedy nearest-match (lowest
Mahalanobis distance within gate) -- fine when detections arrive one at a
time, but sequential greedy resolution has no memory of "already used
this scan", so two near-simultaneous detections near two close tracks can
both legally attach to the same track, leaving the other with no update
that scan (see `tests/test_batch_association.py`'s
`test_sequential_single_calls_can_pile_both_detections_onto_one_track` for
a concrete reproduction).

**`POST /api/detections/batch`** (`app/assignment.py`, global nearest
neighbor via the Hungarian algorithm) fixes this for detections that
genuinely arrive together: post every plot from one sensor's scan/sweep
in one call and they're resolved jointly, enforcing that each track gets
at most one detection per batch, so the pileup above can't happen and two
crossing tracks can't swap identities within that scan. This is still
*not* a full multi-hypothesis tracker (JPDA/MHT) -- it's single-scan
GNN, not probabilistic multi-scan hypothesis management, and it assumes
each track contributes at most one detection per batch (don't mix
multiple sensors' simultaneous reports of the same object into one batch
call expecting both to land on it -- post each sensor's scan separately).

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

**Radar via ASTERIX CAT048** (`app/adapters/asterix_bridge.py`): the
protocol most commercial primary/secondary surveillance radars actually
speak on their network interface, not a proprietary vendor format --
decodes EUROCONTROL's ASTERIX CAT048 (Monoradar Target Reports) from UDP
datagrams via [`asterix4py`](https://pypi.org/project/asterix4py/), which
decodes against EUROCONTROL's own published XML category definitions
rather than a hand-rolled byte parser. Reports azimuth/range like any
other radar, so register the radar's mounting position first (see
Georeferencing above) or every detection is dropped.

```bash
pip install -r requirements-radar.txt
.venv/bin/python -m app.adapters.asterix_bridge --listen-port 8600 --sensor-id radar-1
```

**MAVLink telemetry interception** (`app/adapters/mavlink_bridge.py`): a
MAVLink-speaking drone (ArduPilot, PX4, most hobbyist/commercial flight
controllers) broadcasts its own `GLOBAL_POSITION_INT` over its telemetry
link -- interceptable the same way this tracker's other adapters intercept
RF/ADS-B/camera signal, via [`pymavlink`](https://pypi.org/project/pymavlink/).
**This is not authenticated** -- unlike the signed Remote ID path below, a
MAVLink intercept carries no cryptographic proof of identity, so it's
reported at high confidence (only drones speak MAVLink) but never resolves
to `friendly` on its own.

```bash
pip install -r requirements-mavlink.txt
.venv/bin/python -m app.adapters.mavlink_bridge --source udp:127.0.0.1:14550 --sensor-id mavlink-1
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
what's actually in frame.

**Camera object detection with YOLO** (`app/adapters/camera_yolo.py`): a
real alternative to motion cueing -- runs a YOLO object detector
(`ultralytics`) on each captured frame instead of a background-subtraction
motion blob, so it can actually distinguish object classes (car, person,
dog, bird, ...) and rule out ones that are confidently not aerial contacts,
instead of posting a detection for anything that moves.

```bash
pip install -r requirements-camera.txt
.venv/bin/python -m app.adapters.camera_yolo --source rtsp://192.168.1.50/stream1 \
  --target-lat 51.50 --target-lon -0.10 --model yolov8n.pt
```

**There is no "drone" class in stock COCO-pretrained YOLO weights** -- the
80 COCO classes cover common objects (person, car, airplane, bird, kite,
...) but not drones, because no such public, generically-licensed
dataset/label exists in the standard release. This adapter rules OUT
confidently-non-aerial classes (person, car, dog, ...) and reports a
confident 'bird' match at low confidence (correctly resolving to `bird`,
not `drone`, through the existing thresholds), but anything else in frame
(a kite, an airplane, or any other unrecognized class) is reported as an
unidentified object at moderate confidence, not a confirmed drone. A
deployment wanting real drone/not-drone classification from vision needs a
model fine-tuned on a labeled drone dataset (several exist publicly, e.g.
on Roboflow Universe) -- point `--model` at those weights once you have
them; the adapter works with any YOLO-format model, not just the stock
COCO one. Like `dump1090_bridge.py`, this only runs if you `pip install`
the camera extras -- `ultralytics` (and its `torch` dependency) is not a
core dependency of the API server.

## RF signature fingerprinting

An RF detection whose `raw_data` carries `center_frequency_mhz`,
`bandwidth_mhz`, and (optionally) `frequency_hopping` is matched against a
small library of publicly documented drone control/video link signatures
(`app/rf_signatures.py`) -- DJI OcuSync, DJI Lightbridge, analog FPV video,
and Wi-Fi-based FPV/control links -- instead of trusting a single flat RF
confidence number. This mirrors how real counter-drone RF sensors actually
work: classifying frequency band, channel bandwidth, and hopping behavior
against known signature libraries for common link types, not decoding
encrypted proprietary protocol content. A signature match's confidence is
used (via `app/fusion.py`) whenever it's higher than the sensor's own
reported confidence, so a low-confidence RF detection with a
signal-shape that matches a known drone link still classifies as `drone`.
It cannot decode a link's payload, extract telemetry, or identify a
specific aircraft -- only that its RF envelope is consistent with a known
type of control/video link. The band/bandwidth windows in `SIGNATURES` are
drawn from published consumer/hobbyist RF specifications and are
approximate, not exact per-model specs -- tune them to your own RF
sensor's measured characteristics if you have one.

## Airspace data

By default, zones come from the single hand-seeded polygon in
`app/zones.seed.json` -- fine for a demo, not for representing real
airspace. Two real, publicly published FAA data sources can supplement or
replace it:

**FAA UAS Facility Map** (`app/airspace/faa_uas_facility_map.py`): the
actual published grid of maximum altitudes UAS operators may fly at near
an airport without further LAANC authorization, queried live from FAA's
public, unauthenticated ArcGIS FeatureServer
(`FAA_UAS_FacilityMap_Data`). Each grid cell is imported as a `monitoring`
zone with its ceiling (feet AGL) converted to `max_altitude_m` -- not
`restricted`, since exceeding a facility map ceiling means "this specific
flight needs LAANC authorization," not "an intrusion just happened":

```bash
.venv/bin/python -m app.adapters.faa_zones_import \
  --min-lon -0.5 --min-lat 51.3 --max-lon 0.3 --max-lat 51.7
```

This uses the standard, well-documented ArcGIS REST query contract and a
field name (`CEILING`) confirmed from the layer's public metadata, but
outbound access to arcgis.com wasn't available from the environment this
was built in to run a live import end-to-end -- sanity-check your first
real import against a known airport's published facility map.

**FAA NOTAMs** (`app/airspace/faa_notam.py`, `app/adapters/faa_notam_check.py`):
Notices to Air Missions cover the kind of temporary/event-driven airspace
restriction a static zone file or the facility map's fixed grid can't --
TFRs, a stadium event, a UAS area closed for the day. Requires a free
`client_id`/`client_secret` from the [api.faa.gov](https://api.faa.gov)
developer portal:

```bash
.venv/bin/python -m app.adapters.faa_notam_check \
  --client-id "$DRONE_FAA_NOTAM_CLIENT_ID" --client-secret "$DRONE_FAA_NOTAM_CLIENT_SECRET" \
  --lat 51.5 --lon -0.1 --radius-nm 50
```

**This one genuinely hasn't been validated against a live account** --
both `api.faa.gov` and its developer-registration flow were unreachable
from this environment's network, so unlike every other real-protocol
integration in this README, the request/response shape here is
documented-but-unverified; treat it as a starting point to confirm against
your own registered account, not a proven integration. It also
deliberately returns NOTAMs as a plain list for a human to review rather
than auto-converting them into zones -- NOTAM geometry, when present at
all, isn't reliably a clean polygon the way the facility map's is, and a
wrong guessed restricted-zone shape is worse than no zone.

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

A detection votes `friendly` only if its `raw_data` carries an
`operator_id` matching a registered authorized operator **and** a valid
Ed25519 signature over that operator/detection pair (`app/remote_id.py`,
`app/allowlist.py`) -- a bare, self-reported `operator_id` with no
signature (or a signature from the wrong key) is not enough to grant
`friendly`. Register an operator with a public key, then have their
ground-control software sign each detection with the matching private key:

```bash
python3 -c "from app.remote_id import generate_keypair; print(generate_keypair())"
# -> (private_key_b64, public_key_b64) -- keep the private key with the operator,
# register only the public key here.

curl -X PUT http://127.0.0.1:8000/api/authorized-operators/OP-12345 \
  -H "Content-Type: application/json" -H "X-API-Key: <admin key>" \
  -d '{"name":"Acme Surveying Co.","public_key":"<public_key_b64>"}'
```

```python
from app.remote_id import sign_detection
detection.raw_data["signature"] = sign_detection("OP-12345", detection, private_key_b64)
```

The signed message binds `operator_id`, `sensor_id`, `timestamp`, and
(rounded) `latitude`/`longitude` together, so a captured signature can't be
replayed onto a different detection. This closes the spoofing gap flagged
by the security review: `GET /api/authorized-operators` remains
**admin-only** (not `viewer`) as defense in depth, and `friendly` still
only tempers an incident's severity to `medium` rather than suppressing it
to `low`, since even a correctly-signed claim is a self-report, not
independently-verified evidence like ADS-B. This implements the
cryptographic trust layer, not the ASTM F3411 Remote ID broadcast wire
format itself -- integrating a real Remote ID receiver would decode
broadcasts off-air and feed `operator_id`/`signature` into this same path.

## Operations

- **Rate limiting**: `POST /api/detections` is limited per `sensor_id` by
  an in-memory token bucket (`DRONE_RATE_LIMIT_PER_SECOND`/`_BURST`) --
  a backstop against a malfunctioning or malicious sensor, not a normal-load
  limit. Returns 429 when exceeded.
- **Retention**: set `DRONE_DETECTION_RETENTION_DAYS` to periodically purge
  detections older than that many days (a background task sweeps every
  `DRONE_RETENTION_SWEEP_INTERVAL_SECONDS`, disabled by default -- keeps
  everything forever, the previous behavior).
- **Track history/replay**: `GET /api/tracks/{track_id}/history` returns
  every detection that fed a track, oldest first -- a full replay of where
  it actually was over time, for post-incident review. Works the same for
  a closed/lost track as an active one: detection rows aren't deleted when
  a track closes, only purged by age via `DRONE_DETECTION_RETENTION_DAYS`
  above, so there's no separate "archive" to look in -- if retention hasn't
  purged it, the history is still there. The dashboard draws it as a dashed
  trail on the map when you select a track.
- **Track history export**: `GET /api/tracks/{track_id}/history/export?format=gpx|kml|csv`
  returns the same history as a downloadable file for an external tool --
  GPX or KML for a GIS/mapping application (Google Earth, QGIS, ...), CSV
  for a spreadsheet -- instead of only being usable from this app's own
  API/dashboard.
- **Structured logging**: `DRONE_LOG_FORMAT=json` emits one JSON object per
  log line instead of human-readable text, for log aggregators.
- **Metrics**: `GET /api/metrics` in Prometheus exposition format --
  detections ingested (by sensor type), incidents opened (by type/severity),
  rate-limit rejections, and live gauges for active tracks / open incidents.
- **Outbound alerting**: set `DRONE_WEBHOOK_URLS` (comma-separated) to POST
  each incident's JSON to one or more generic webhooks when it opens.
  On top of that, `app/alerting.py` adds severity-routed integrations for
  Slack, PagerDuty, and SMS (via Twilio), each independently configured
  (empty/unset = disabled) with its own minimum-severity threshold -- an
  escalation policy, so e.g. every incident can reach Slack for situational
  awareness while only `high`+ pages PagerDuty and only `critical` sends an
  SMS, instead of one severity treatment for every channel:

  | Channel | Enable with | Threshold var (default) |
  |---|---|---|
  | Slack | `DRONE_SLACK_WEBHOOK_URL` | `DRONE_SLACK_MIN_SEVERITY` (`low`) |
  | PagerDuty | `DRONE_PAGERDUTY_ROUTING_KEY` | `DRONE_PAGERDUTY_MIN_SEVERITY` (`high`) |
  | SMS (Twilio) | `DRONE_TWILIO_ACCOUNT_SID`/`_AUTH_TOKEN`/`_FROM_NUMBER` + `DRONE_SMS_TO_NUMBERS` | `DRONE_SMS_MIN_SEVERITY` (`critical`) |

  Every channel is best-effort with a short timeout (`DRONE_ALERT_TIMEOUT_SECONDS`)
  -- a dead or misconfigured integration logs a warning and is skipped, it
  can't block incident handling or take the other channels down with it.
- **Message-queue fan-out** (`app/queue_publisher.py`, optional): set
  `DRONE_NATS_URL` (e.g. `nats://broker-host:4222`) to additionally publish
  a JSON copy of every ingested detection and opened incident onto a NATS
  core subject (`DRONE_NATS_DETECTION_SUBJECT`/`_INCIDENT_SUBJECT`,
  defaulting to `drone.detections`/`drone.incidents`) alongside the normal
  in-process handling -- unset (the default) disables it entirely, with no
  behavior change. This is scaffolding for a future multi-site or
  high-throughput deployment to build on: a consumer process (or several,
  elsewhere) can subscribe to these subjects for fan-in aggregation,
  cross-site correlation, or a separate analytics pipeline, without
  touching the ingest API or the tracker. It does **not** make
  ingest/association/fusion itself queue-based -- that stays exactly the
  synchronous single-process design described above, which is the right
  call for a single site's real-time load; only publishing this
  supplementary copy is new. Implemented as a minimal NATS core PUB client
  over a raw socket rather than a full client library, so it adds no new
  dependency.

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
| `DRONE_SLACK_WEBHOOK_URL` | *(unset)* | Slack incoming-webhook URL for incident alerts |
| `DRONE_SLACK_MIN_SEVERITY` | `low` | Minimum incident severity that reaches Slack |
| `DRONE_PAGERDUTY_ROUTING_KEY` | *(unset)* | PagerDuty Events API v2 routing key |
| `DRONE_PAGERDUTY_MIN_SEVERITY` | `high` | Minimum incident severity that pages PagerDuty |
| `DRONE_TWILIO_ACCOUNT_SID` / `_AUTH_TOKEN` / `_FROM_NUMBER` | *(unset)* | Twilio credentials for SMS alerts |
| `DRONE_SMS_TO_NUMBERS` | *(unset)* | Comma-separated destination numbers for SMS alerts |
| `DRONE_SMS_MIN_SEVERITY` | `critical` | Minimum incident severity that sends an SMS |
| `DRONE_ALERT_TIMEOUT_SECONDS` | `5` | Per-request timeout for Slack/PagerDuty/SMS alerts |
| `DRONE_NATS_URL` | *(unset)* | NATS broker URL to additionally publish detections/incidents to; unset disables it |
| `DRONE_NATS_DETECTION_SUBJECT` | `drone.detections` | NATS subject each ingested detection is published to |
| `DRONE_NATS_INCIDENT_SUBJECT` | `drone.incidents` | NATS subject each opened incident is published to |
| `DRONE_NATS_CONNECT_TIMEOUT_SECONDS` | `2` | Connect/send timeout for the NATS publisher |
| `DRONE_FAA_NOTAM_CLIENT_ID` / `_CLIENT_SECRET` | *(unset)* | api.faa.gov developer credentials for `app/adapters/faa_notam_check.py` |
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
  tracking.py            Track association (single-detection greedy + batch GNN), update, expiry
  kalman.py               Constant-velocity Kalman filter (no numpy dependency)
  imm.py                  IMM (Interacting Multiple Model) filter: blends CRUISE + MANEUVER modes
  assignment.py            Hungarian algorithm (global nearest neighbor for batch association)
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
