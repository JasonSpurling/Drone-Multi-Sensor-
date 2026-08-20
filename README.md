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

**Linting and type-checking** (`ruff`, `mypy` -- also run in CI as a
separate `lint` job): see `pyproject.toml`'s `[tool.ruff]` section for the
deliberate rule selection and why a few categories (naive-datetime
warnings, FastAPI's `Depends()`/`Query()` default-argument pattern) are
intentionally excluded rather than fought.

```bash
pip install -r requirements-dev.txt
ruff check app/ tests/ tests_e2e/
mypy app/ --ignore-missing-imports
```

**Browser-driven dashboard tests** (`tests_e2e/`, a separate CI job):
launches the real dashboard in a real headless Chromium via Playwright and
clicks around in it -- selecting a track, dragging the Playback scrubber,
toggling map layers -- catching what `tests/`'s API-level tests can't
(this is exactly how the zoom-control/map-layer-toggles click-interception
bug documented above was actually found). See `tests_e2e/README.md`.

```bash
pip install -r requirements-e2e.txt
playwright install chromium
python -m pytest tests_e2e/ -v
```

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

## Behavioral analysis

"Is it in a zone" was the only question incidents used to ask. `app/behavior.py`
adds pattern-of-life analysis on the track history this app now records:

- **Loitering** -- checked on every update for a track's own recent history:
  has it stayed within `DRONE_LOITERING_RADIUS_M` (default 75m) for at
  least `DRONE_LOITERING_MIN_DURATION_S` (default 120s) -- circling/
  hovering over one spot rather than transiting through.
- **Formation** -- checked on a periodic sweep (`DRONE_BEHAVIOR_SWEEP_INTERVAL_SECONDS`,
  default every 30s) across all active tracks: groups moving together
  within `DRONE_FORMATION_MAX_SPACING_M` on similar heading/speed
  (`DRONE_FORMATION_HEADING_TOLERANCE_DEG`/`DRONE_FORMATION_SPEED_TOLERANCE_MPS`)
  -- the pattern a swarm shows, not a coincidental cluster.
- **Shadowing** -- also on the periodic sweep: pairs of tracks where one
  has stayed within `DRONE_SHADOWING_MAX_DISTANCE_M` of the other for at
  least `DRONE_SHADOWING_MIN_DURATION_S` -- sustained escort, not a brief
  pass. This works for any two tracks, not literally "a drone following a
  person" -- this app has no independent person-detection sensor, so it
  can't verify a shadowed target's identity; it's a general shadowing
  pattern between whatever two tracks are being compared.

Each opens a `loitering`/`formation`/`shadowing` incident (not tied to any
zone -- `zone_id` is null) through the same incident pipeline as zone
incursions: deduplicated, alerted, published to any configured NATS/CoT
output. Formation and shadowing run on a periodic sweep rather than
per-detection because they compare *multiple* active tracks against each
other -- a different computational shape than a per-track check.

## Cursor on Target (CoT) output

Nothing in this app talked to a broader command-and-control picture
before. `app/cot.py` generates real Cursor on Target XML events -- the
open format the TAK ecosystem (ATAK/WinTAK/iTAK, FreeTAKServer) uses,
and not exclusively military: TAK is also used by wildland firefighting,
search and rescue, and other civil public-safety agencies. Set
`DRONE_COT_UDP_HOST` to broadcast a CoT event over UDP for every track
update, feeding this tracker's output into a TAK Server or any
CoT-consuming client (e.g. ATAK's own UDP CoT input):

```bash
DRONE_COT_UDP_HOST=192.168.1.100 DRONE_COT_UDP_PORT=6969 .venv/bin/python main.py
```

Built directly with the standard library rather than taking on `pytak`
(the standard Python CoT/TAK client) as a runtime dependency for a
handful of XML attributes -- but the schema (event version/type/uid/how/
time/start/stale; a point child with lat/lon/hae/ce/le; a detail child
with a contact callsign) was verified against pytak's actual source
during development, and its output was cross-checked byte-for-byte
against what pytak itself generates for the same inputs, not assumed
from memory.

A track's classification maps to a CoT affiliation -- `friendly`/`aircraft`
to friendly, `bird` to neutral, `drone`/`unknown` to **unknown, never
hostile**: declaring hostile is a positive-identification decision an
operator makes, not something a sensor-fusion pipeline should
auto-assert. This only sends a CoT event outbound over plain UDP, the
simplest and most universally supported transport (what ATAK's own
default UDP input listens for) -- not a full TLS-secured TAK Server
client (mutual-auth enrollment, packaged data transfer), which a
deployment with an actual TAK Server would need to add on top of this.

**Link-16 is not implemented, and won't be.** Unlike CoT (an open,
unclassified, widely-implemented public-safety standard), Link-16 is a
certified military tactical data link requiring MIDS/JTIDS terminal
hardware and controlled cryptographic keying -- not something buildable
or appropriate to build in software without that hardware and the
authorization to operate it. CoT closes the realistic version of this
gap; Link-16 is a different kind of thing entirely, not a harder version
of the same task.

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

## Acoustic direction finding

An acoustic detection used to be a single flat confidence number with no
direction at all. `app/acoustic_beamforming.py` adds real delay-and-sum
steered-response-power beamforming -- the same fundamental idea as RF
direction-finding (multiple receive points, arrival-time differences
reveal direction) applied to sound instead of radio -- to estimate an
actual bearing from a small microphone array:

```bash
pip install -r requirements-acoustic.txt
.venv/bin/python -m app.adapters.acoustic_array_bridge \
  --sensor-id acoustic-1 \
  --mic-positions '[[0.032,0.032],[0.032,-0.032],[-0.032,-0.032],[-0.032,0.032]]' \
  --assumed-range-m 150 --confidence 0.6
```

Pure numpy math (a core dependency, unlike the hardware-specific adapters
elsewhere in this README), verified against synthetic signals with a
known true bearing computed independently of the module itself --
placing an actual point source and deriving each mic's exact Euclidean
propagation delay from scratch, not by calling the module's own delay
function to generate its own "ground truth" (which would be circular).
That independent check caught a real sign bug during development that a
circular test would have missed entirely: the first version had mics
*closer* to the source hearing the wavefront *later*, silently flipping
every bearing estimate 180 degrees.

**Accuracy depends on array size and signal bandwidth.** Empirically
(see `tests/test_acoustic_beamforming.py`): a compact ~6cm array
(ReSpeaker-scale) resolved bearing within 20 degrees against a
band-limited source resembling real rotor/propeller noise; a larger
~30cm array got under 6 degrees. Feeding it raw broadband audio instead
of band-passing to the low-frequency range rotor noise actually lives in
degrades accuracy well beyond that on a larger array (classic spatial
aliasing once mic spacing exceeds roughly half the shortest wavelength
present) -- this isn't a corner case to ignore, it's the difference
between a working estimate and a badly aliased one.

**A single array can't measure range**, only bearing -- the same
limitation a lone RF direction-finder has without a second station to
triangulate against. The bridge reports an operator-supplied
`--assumed-range-m` rather than a measured one (flagged as such in
`raw_data`), the same honest compromise the camera-motion adapter makes
for its own inability to measure range. `bearing_confidence` (how sharp
the *direction* estimate is) and `--confidence` (a separate, manual "is
this actually a drone sound" estimate) are deliberately not conflated --
they answer different questions.

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

## DJI DroneID payload decoding

RF signature fingerprinting above stops at "this looks like a DJI
control/video link" -- it can't decode the payload. DJI OcuSync actually
broadcasts real telemetry unencrypted: the drone's own GPS position, the
*operator's* GPS position, a serial number, and a home point. That's a
disclosed security finding (peer-reviewed at NDSS 2023, "Drone Security
and the Mysterious Case of DJI's DroneID"), and it's exactly what a
commercial DJI AeroScope appliance decodes.

Decoding that payload needs real SDR hardware and physical-layer
demodulation -- entirely outside what this app (or any pip package) does.
`app/adapters/dji_droneid_bridge.py` is a bridge, the same role
`dump1090_bridge.py` plays for ADS-B: it consumes the JSON-lines output of
[`RUB-SysSec/DroneSecurity`](https://github.com/RUB-SysSec/DroneSecurity),
the open-source receiver published with that paper (tested against an
Ettus USRP B205-mini), and posts each decoded packet as a detection:

```bash
# Set up and run DroneSecurity separately per its own README, then:
./src/droneid_receiver_live.py | .venv/bin/python -m app.adapters.dji_droneid_bridge --sensor-id dji-rf-1
```

The drone's own position becomes the tracked detection; the operator's
position, serial number, and home point are carried in `raw_data` rather
than as a separate tracked entity -- a person's real-time location is
genuinely sensitive, and this is metadata for a security response
(the same use case AeroScope is sold for), not something to spin up its
own track for. A packet whose reported CRC doesn't match what
DroneSecurity itself calculated is dropped rather than trusted.

**What's verified here and what isn't**: the JSON field names this bridge
parses match DroneSecurity's own documented example output. What couldn't
be verified in the environment this was built in: an actual live SDR, a
real DroneID capture, or DJI's own spec -- there isn't one, OcuSync/
DroneID is proprietary and undocumented, and RUB-SysSec's paper is an
independent reverse-engineering effort, not an official DJI
specification, so exact fields available may vary by drone model/firmware.
Sanity-check your first real decoded packet before relying on this.

## Real ASTM F3411 Remote ID reception

`app/remote_id.py` (see the Classification fusion section below) is a
signed-claim scheme this app defines -- verifying a cryptographic
assertion, not anything a real drone actually broadcasts. Every drone
over 250g sold in the US/EU is now separately required to broadcast real
**ASTM F3411 Remote ID** over Bluetooth or Wi-Fi, and this app can receive
that directly too, via `app/adapters/astm_remote_id_ble_bridge.py`:

```bash
pip install -r requirements-remoteid.txt
sudo .venv/bin/python -m app.adapters.astm_remote_id_ble_bridge --sensor-id remote-id-1
```

Any standard Bluetooth adapter works -- no SDR needed (unlike the DJI
DroneID bridge above); Remote ID's whole design point is that anyone can
passively receive it. Decoding uses
[`dtpyodid`](https://github.com/dronetag/python-odid), a real Python
implementation of the ASTM F3411 message formats from Dronetag (a
commercial Remote ID hardware vendor), verified here by round-tripping
real messages through the library's own encoder/decoder and cross-checking
the Bluetooth framing against `opendroneid/transmitter-linux`'s reference
implementation -- not a byte-offset parser guessed from memory. Over
Bluetooth 4 Legacy Advertising a transmitter sends one message per
broadcast (position, operator ID, serial number, ...), cycling through
them, so this bridge accumulates a device's state across several
broadcasts before it has enough to post a detection.

**This is not authenticated.** Unlike `app/remote_id.py`'s signature
scheme, ASTM F3411 itself has no cryptographic authentication of its
OperatorID field -- a real, published limitation of the standard, not
something a receiver can fix. A broadcast claiming a given operator ID is
exactly as spoofable as the unsigned scheme this project's security
review already closed for its own signed-claim path, so receiving a real
broadcast never resolves to `friendly` on its own; cross-reference its
OperatorID against a separately verified identity if you want that.

## Slew-to-cue

Every adapter in this app ingests a static/fixed-position feed -- nothing
has ever pointed a physical sensor anywhere. `GET
/api/tracks/{track_id}/cue/{camera_sensor_id}` closes that gap: it
computes the pan/tilt angles a PTZ (pan-tilt-zoom) camera registered as
`camera_sensor_id` needs to point at a track detected by a *different*
sensor -- an RF direction-finder, an acoustic array (see Acoustic
direction finding above), radar, anything -- so a bearing from one sensor
can swing a camera mounted somewhere else entirely onto the target,
instead of a human operator manually panning to follow a cue.

```bash
curl "http://127.0.0.1:8000/api/tracks/12/cue/ptz-cam-1"
# {"pan_deg": 51.2, "pan_relative_deg": 51.2, "tilt_deg": 6.1, "distance_m": 1775.6, ...}
```

The geometry (`app/slew_to_cue.py`) is plain trigonometry -- great-circle
bearing and elevation angle from the camera's registered position to the
track's current one -- computed fresh on every request, not cached or
pushed; poll it as often as your camera's slew rate can usefully act on.
`pan_relative_deg` already accounts for the camera's own mounting
orientation (`azimuth_reference_deg`, the same field every other
azimuth-reporting sensor registers) -- it's the number a PTZ camera whose
pan axis is zeroed to its own boresight actually needs, not `pan_deg`
(the absolute compass bearing).

**Driving a real camera**: `app/adapters/onvif_ptz_bridge.py` polls the
cue endpoint and issues real ONVIF (the open IP-camera control standard
most commercial PTZ cameras support) `AbsoluteMove` commands, via
`onvif-zeep-async` -- the actively maintained ONVIF client library Home
Assistant's own ONVIF integration uses, not a hand-rolled SOAP client.

```bash
pip install -r requirements-ptz.txt
.venv/bin/python -m app.adapters.onvif_ptz_bridge \
  --track-id 12 --cueing-camera-sensor-id ptz-cam-1 \
  --camera-host 192.168.1.50 --camera-user admin --camera-password secret
```

**This could not be tested against real PTZ hardware** in the environment
this was built in (no camera here) -- the geometry and the API endpoint
are fully verified (see `tests/test_slew_to_cue.py` and
`tests/test_slew_to_cue_api.py`), but the ONVIF bridge's coordinate
normalization (ONVIF `AbsoluteMove` ranges are camera-specific, queried
from the camera itself via `GetConfigurationOptions`, not a fixed unit --
correctly handled here, per the ONVIF spec, rather than assumed) hasn't
been confirmed against a real camera's actual reported ranges. Verify
against your own hardware before relying on it.

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

An azimuth/range-only sensor (a GPS-less radar or RF direction-finder) has
no lat/lon of its own to sign, so it signs with position `None`/`None`;
`app/georeference.py` fills in an estimated position server-side
afterwards so the detection can still be tracked and mapped, and marks the
detection `georeferenced=True` when it does. `canonical_message()` binds
to `None`/`None` for a `georeferenced` detection rather than that
estimate, so the signature verifies against what the sensor actually
signed. `georeferenced` is reset server-side on every ingest
(`app/api/detections.py`) regardless of what a client sends, so a client
can't set it to fake a position out of the signature's scope.

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
  trail on the map when you select a track, and the Selected Track panel's
  **Playback** scrubber lets you step (or auto-play) through that history --
  dragging the slider moves a dashed "ghost" marker to the track's position
  at that point in time, dimming its live marker for context; the Live
  button snaps back to its current position.
- **Map trails and projected path**: every visible track (not just the
  selected one) draws a short fading trail from its last several polled
  positions, so recent motion/direction is readable at a glance without
  selecting each track in turn. Each track with a heading and speed above
  1 m/s also draws a dotted line projecting 30s ahead along a real
  great-circle bearing (the same geodesic math `app/georeference.py` uses
  server-side, not a flat lat/lon approximation) -- a visual aid, not tied
  to `DRONE_PREDICTIVE_HORIZON_SECONDS`. Both are togglable via the map's
  Trails/Vectors checkboxes.
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

## Load testing

`scripts/load_test.py` measures real throughput/latency against a running
instance -- not a pass/fail CI gate (the numbers are a property of the
machine running it, not a fixed correctness threshold), just a documented,
reproducible way to generate them:

```bash
python scripts/load_test.py --url http://127.0.0.1:8000 --mode single --sensors 20 --duration 15
python scripts/load_test.py --url http://127.0.0.1:8000 --mode batch --batch-size 30 --requests 100
```

**Last recorded results** (this container's CPU, single instance, rate
limiting raised via `DRONE_RATE_LIMIT_PER_SECOND`/`_BURST` to measure the
actual processing ceiling rather than the deliberate per-sensor throttle --
see Operations above):

| Backend | Mode | Throughput | p50 / p95 / p99 latency |
|---|---|---|---|
| SQLite | single (20 concurrent sensors) | 92.6 req/s | 211 / 258 / 279 ms |
| SQLite | batch (30 plots/request) | 87.5 plots/s | 330 / 394 / 1244 ms |
| PostgreSQL | single (20 concurrent sensors) | 67.5 req/s | 288 / 341 / 359 ms |
| PostgreSQL | batch (30 plots/request) | 69.0 plots/s | 420 / 514 / 848 ms |

Two things worth understanding about these numbers, not just the numbers
themselves:

- **PostgreSQL is not faster here, and that's expected, not a bug.**
  `app.tracking`'s `_association_lock` guards the read-then-write
  detection/track/incident sequence against FastAPI's threadpool running
  concurrent requests, serializing *all* detection processing through one
  process-wide lock regardless of backend. That means this benchmark never
  exercises PostgreSQL's actual advantage (concurrent writers from multiple
  processes/replicas, which SQLite cannot do at all); it only measures
  per-query round-trip cost, where SQLite's in-process file access beats a
  TCP round-trip to Postgres. Point `DRONE_DATABASE_URL` at PostgreSQL for
  concurrent-write *safety* (multiple app instances, a separate reporting
  connection) or its operational maturity (replication, backups, monitoring
  tooling), not for single-instance throughput.
- **Batch throughput degrades as active track count grows** (visible in the
  batch mode's much higher p99 vs p50 -- a single slow outlier well above
  the rest): `associate_detections_batch`'s Hungarian assignment builds an
  `n × m` cost matrix (n = detections in this batch, m = active tracks) and
  solves it in better-than-cubic but still superlinear time in the active
  track count. Fine at the track counts a single site actually sees; a
  deployment expecting a very large number of simultaneously active tracks
  should re-measure at that scale specifically, not extrapolate from these
  numbers.

Reproduce these (or measure your own deployment's real numbers, which will
differ by hardware): create a fresh scratch database, start the app against
it, then run both `--mode single` and `--mode batch` above.

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
| `DRONE_LOITERING_RADIUS_M` | `75` | Max spread for a track to count as loitering |
| `DRONE_LOITERING_MIN_DURATION_S` | `120` | Min sustained duration to flag loitering |
| `DRONE_SHADOWING_MAX_DISTANCE_M` | `30` | Max distance between two tracks to count as shadowing |
| `DRONE_SHADOWING_MIN_DURATION_S` | `60` | Min sustained duration to flag shadowing |
| `DRONE_FORMATION_MAX_SPACING_M` | `100` | Max spacing between tracks to count as one formation |
| `DRONE_FORMATION_HEADING_TOLERANCE_DEG` | `15` | Max heading difference to count as moving together |
| `DRONE_FORMATION_SPEED_TOLERANCE_MPS` | `2` | Max speed difference to count as moving together |
| `DRONE_BEHAVIOR_SWEEP_INTERVAL_SECONDS` | `30` | How often the formation/shadowing cross-track sweep runs |
| `DRONE_COT_UDP_HOST` | *(unset)* | TAK endpoint host to broadcast CoT events to; unset disables it |
| `DRONE_COT_UDP_PORT` | `6969` | TAK endpoint UDP port |
| `DRONE_COT_STALE_SECONDS` | `60` | How long a CoT event is valid before a TAK client greys it out |
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
tests_e2e/                 Browser-driven dashboard tests (Playwright, separate CI job -- see its README.md)
simulator.py               Posts realistic detections against a running server
main.py                    Entrypoint (python main.py)
Dockerfile                 Single-stage container build
```
