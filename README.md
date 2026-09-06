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
run -- including the `data/` directory itself if it doesn't exist yet
(it's gitignored, so a fresh clone/download never has it). Open the URL
in a browser for the dashboard, or `/docs` for interactive API docs.

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
bug documented above was actually found). Also includes pixel-level visual
regression tests for a few static bits of UI chrome, catching a CSS
layout break that every behavioral/ARIA assertion would still pass (see
`tests_e2e/README.md`'s "Visual regression tests" section). See
`tests_e2e/README.md`.

```bash
pip install -r requirements-e2e.txt
playwright install chromium
python -m pytest tests_e2e/ -v
```

**Browser coverage is Chromium-only** -- a deliberate scope choice (one
browser to install and run in CI, matching most teams' practice), not an
oversight, but worth naming plainly: a dashboard CSS/layout bug specific
to Firefox or Safari (e.g. a flexbox or `:focus-visible` quirk) would pass
this suite and CI undetected. If that risk matters for your deployment
(e.g. operators are mandated onto a specific non-Chromium browser),
Playwright can run the same suite against `firefox`/`webkit` by changing
`p.chromium.launch(...)` to `p.firefox.launch(...)`/`p.webkit.launch(...)`
in `tests_e2e/conftest.py`'s `browser` fixture -- nothing else in the
suite is Chromium-specific.

## Deployment

**Quick single-container run** (SQLite, fine for trying it out):

```bash
docker build -t drone-multi-sensor .
docker run -p 8000:8000 -v drone-data:/app/data drone-multi-sensor
```

The image binds to `0.0.0.0:8000` inside the container (so `docker run -p`
can reach it) and stores the SQLite database in `/app/data` — the `-v` above
keeps it across container restarts. Set `-e DRONE_API_KEY=<key>` if the
container's port will be reachable beyond your own machine -- if you don't,
a startup log line warns loudly (`DRONE_HOST=0.0.0.0` with no key
configured is treated as a mistake, not a supported deployment shape) and
the app still starts, since refusing to start would break plain local
development, where that combination is fine. The image
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

**Graceful shutdown**: in-flight HTTP requests are drained by uvicorn
itself on SIGTERM before the process exits -- nothing app-specific needed
there. WebSocket connections (`GET /ws/live`) are different: they have no
natural end the way a request does, so a client that never disconnects on
its own would otherwise leave uvicorn waiting indefinitely for it during a
graceful stop, getting cut off by Docker/systemd's own SIGKILL timeout
instead of closing cleanly. `app.live.close_all()` (called from the
app's own shutdown handler, before the retention/behavior background
tasks are cancelled) explicitly signals every connected client to close
the moment shutdown starts, so this doesn't depend on the client's own
behavior.

None of this replaces the "what's missing to run this on real hardware"
gaps (sensor/RF front-end integration, TLS being opt-in rather than
default, time sync across physical sensors, ...) -- it's specifically
about running the software itself reliably once you do have real sensors
feeding it.

### Running multiple replicas behind a load balancer

**Requires PostgreSQL, not SQLite.** SQLite's single-writer model makes it
inherently single-process (see the Database section) -- there's no correct
way to point two replicas at the same SQLite file. Everything below
assumes the "Full stack with PostgreSQL" setup above.

**Why this is safe at all**: `app/tracking.py`'s detection-association
critical section is guarded by an in-process `threading.Lock` *and*, when
the DB dialect is PostgreSQL, `app/cluster_lock.py`'s
`cluster_association_lock()` -- a session-level `pg_advisory_lock` held for
the same critical section. Two detections for the same object landing on
two different replicas at the same instant now serialize against each
other cluster-wide instead of each replica only serializing against
itself, which is what stops them from independently concluding "no
existing track" and each creating a duplicate. This is automatic --
nothing to configure -- as long as every replica talks to the same
Postgres database.

Verified end-to-end, not just argued from reading the code:
`tests/test_multi_process_failover.py` spawns two real `uvicorn`
processes against one real PostgreSQL database (Docker Compose's exact
topology, just without the load balancer in front) and fires pairs of
near-simultaneous "first sighting" detections at each, one replica per
detection, for ten independent objects. It asserts exactly ten tracks
result -- confirmed this actually catches the race it's for by
temporarily disabling `cluster_association_lock()` locally and watching
the same test fail with 15 tracks instead of 10. Requires
`DRONE_TEST_DATABASE_URL` pointed at PostgreSQL (skipped otherwise, same
as every other PostgreSQL-only test -- see "Tests" above).

**Process manager**: with Docker Compose, `docker compose up -d --scale
app=3` runs 3 copies of the `app` service -- but first remove `app`'s
`ports:` mapping in `docker-compose.yml` (only one container can bind a
given host port, so a fixed mapping breaks past 1 replica) and instead
put a reverse proxy in front that load-balances to `app:8000`: Compose's
built-in DNS resolves that service name to all 3 replicas' addresses, and
Caddy/nginx round-robin across them automatically -- no static list of
replica addresses to maintain as you scale up or down. The commented-out
`caddy` service in `docker-compose.yml` already does this (its
`reverse_proxy app:8000` line needs no change to pick up added replicas).
On bare metal/VMs, copy
`deploy/drone-multi-sensor.service` to a per-instance unit name (e.g.
`drone-multi-sensor@.service` with `%i` in `ExecStart`'s `--port`, or just
several differently-named copies each with its own `--port` and
`EnvironmentFile`), one per replica, all pointing `DRONE_DATABASE_URL` at
the same Postgres instance.

**Load balancer health-check wiring**: point the LB's health check at each
replica's `GET /api/health`, not a raw TCP check -- it does a real
`SELECT 1` and returns 503 if the database is unreachable, so the LB
routes around a replica that's up but can't actually serve requests (e.g.
during a Postgres failover). Don't route traffic to a replica until its
first `/api/health` call succeeds.

**`DRONE_DB_POOL_SIZE` sizing per replica**: each replica's connection
pool can grow up to `DRONE_DB_POOL_SIZE + DRONE_DB_MAX_OVERFLOW`
connections to Postgres (defaults: 5 + 10 = 15). That's *per replica* --
with N replicas, Postgres needs to accept at least
`N * (DRONE_DB_POOL_SIZE + DRONE_DB_MAX_OVERFLOW)` concurrent connections,
plus headroom for anything else connected to the same database (backups,
`psql`, a metrics exporter). Stock `postgres:16-alpine`'s default
`max_connections` is 100 -- at the defaults that's already enough for 6
replicas; past that, either raise `max_connections` (and Postgres's
`shared_buffers`/memory alongside it) or lower `DRONE_DB_POOL_SIZE` per
replica so the product still fits.

## API

| Endpoint | Description |
|---|---|
| `GET /api/health` | Liveness check (unauthenticated) |
| `GET /api/metrics` | Prometheus metrics (unauthenticated) |
| `POST /api/detections` | Ingest one detection; runs georeferencing, IMM track association, classification fusion, and zone-incident checks |
| `POST /api/detections/batch` | Ingest simultaneous detections (e.g. one radar scan's plots), resolved jointly via global nearest neighbor |
| `GET /api/detections` | Raw ingested detections (`?sensor_id=`, `?track_id=`, `?start=`, `?end=`, `?limit=`, `?offset=`, freely combined) -- for sensor-level QA/debugging without a track id in hand already, or a bounded time slice once you do; see `GET /api/tracks/{id}/history` below for one track's own full path |
| `GET /api/tracks` / `GET /api/tracks/{id}` | List or fetch tracks (`?status=active\|lost\|closed`, `?limit=`, `?offset=`) |
| `GET /api/incidents` | List incidents (`?status=open\|acknowledged\|resolved`, `?limit=`, `?offset=`) |
| `POST /api/incidents/{id}/acknowledge` | Acknowledge an open incident |
| `POST /api/incidents/{id}/resolve` | Resolve an incident |
| `GET /api/zones` | List active zones (`?include_inactive=true` for the zone-management UI, which also needs to find and reactivate a deactivated one) |
| `POST /api/zones` | Create a zone (admin) |
| `PUT /api/zones/{id}` | Update a zone -- polygon, type, altitude band, active state (admin) |
| `GET /api/sensors` | Per-sensor health: online/stale/offline derived from each sensor's most recent detection, plus a `missing` entry for any registered sensor that's never actually reported one |
| `GET /api/sensor-registrations` | List registered sensors (position/orientation used for georeferencing) |
| `PUT /api/sensor-registrations/{sensor_id}` | Register/update a sensor's fixed position and orientation (admin) |
| `GET /api/authorized-operators` | List authorized ("friendly") drone operators (admin) |
| `PUT /api/authorized-operators/{operator_id}` | Register/update an authorized operator (admin) |
| `GET /api/audit-log` | Who did what admin action, when (admin) |
| `GET /api/reports/incidents` | Aggregate rollup (counts by type/severity/status, resolution-time stats, daily trend) over `?start=`/`?end=` |
| `GET /api/reports/incidents/export` | The same date range's incidents as a downloadable CSV |
| `PUT /api/detections/{id}/label` | Set (or, with `{"label":null}`, clear) an operator's ground-truth label for one detection (operator/admin) |
| `GET /api/ml/training-data/export` | Every labeled detection in this site as a CSV, in the exact shape `app/ml/train.py --csv` expects |
| `GET /api/incidents/{id}/report` | One incident's full after-action story -- what was seen, when, by which sensors, how classified, how responded to (see "Incident reporting" below) |
| `GET /api/admin/keys` | Every configured key's label/role/site/expiry plus last-used time and use count, never the raw key (admin) |
| `GET /ws/live` | WebSocket: pushes `track_update`/`incident_opened` events in near-real-time (`?api_key=` for a key-authenticated deployment; see "Live updates" below) |

### Theme

The dashboard defaults to dark (unchanged regardless of OS/browser theme
preference -- a deliberate choice for a monitoring UI meant to be watched
for long stretches). The sun/moon toggle in the top bar switches to a
light theme instead, remembered per-browser via `localStorage` so it
persists across reloads. The map/track-visualization layer itself always
stays dark in either theme, matching how most mapping dashboards keep
plotted symbology legible against a fixed dark base rather than flipping
it with the surrounding chrome.

### Track search

The Tracks panel's search box matches more than the track's numeric ID or
UID -- also its classification (`drone`, `aircraft`, ...), status
(`active`/`lost`/`closed`), and, when the sensor data actually reported
one, the aircraft category (both the raw ICAO code and its human-readable
label, e.g. `A7` or `rotor` both match a rotorcraft track). Multiple
space-separated terms are ANDed ("drone lost" finds a lost drone without
needing the terms in any particular order), and it combines with the
classification chips above it rather than replacing them. Press `/` from
anywhere on the page to jump straight to the search box, `Esc` while it
has focus to clear it, or click the &times; button that appears once
there's something to clear.

### Track list: alert indicator and sort

A track with an active (non-resolved) incident now shows a small colored
dot on its thumbnail in the Tracks list, colored by that incident's
severity (hovering it names the incident count/severity) -- an operator
can see which track triggered an alert without switching to the Alerts
panel. A "Sort" control next to the classification chips reorders each
classification group by "Most recent" (default), "Alerts first",
"Altitude: high to low", or "Speed: fastest first"; a track with no
altitude/speed known always sorts after every track that has one, never
implied to be on the ground or stationary just to fit a sort. Sorting
only ever reorders *within* a classification group, never across groups
-- the drone/aircraft/bird/... grouping is the primary, deliberate
ordering for a counter-drone operator, so an alerting or fast-moving
track never jumps into a different group's position and gets mistaken
for a different kind of object. The choice is remembered per-browser
(like the theme toggle) so it persists across reloads.

Each card also shows a **Duration** (`first_seen` -> now, real elapsed
time, not a countdown) and, when a registered camera is near enough to
cue on that track (the same `nearestCameraSensor()` lookup the details
panel's Live view tab uses), a **PTZ** badge -- both purely derived from
existing data, no new tracking state.

### Verified/Unverified tracks

The Tracks panel splits into two sub-tabs, **Verified** and **Unverified**
(with a live count on each), before the usual classification groups. A
track counts as Verified once 2+ distinct sensor types have independently
reported on it (`INCIDENT_CORROBORATION_MIN_SENSOR_TYPES`, the same
threshold `app.incidents` already uses to escalate an incident's
severity) -- `corroborating_sensor_types`/`verified` on `GET
/api/tracks(/{id})` and `POST /api/tracks/{id}/classify`, computed fresh
at read time, never stored. A brand-new, single-sensor track is
genuinely Unverified until a second sensor type corroborates it, so the
panel defaults to the Unverified tab -- that's where a fresh detection
actually shows up first.

This is independent of a separate Verified/Unverified filter at the
bottom of the map itself, which controls which diamonds plot there
without touching which half of the *list* is showing -- one diamond chip
per classification actually present in each bucket (not a flat two-way
toggle), each independently togglable, plus an ALL reset per row; every
chip is on by default.

`contributing_sensor_types` -- which sensor types, not just how many --
is the same corroboration set, surfaced on the map itself in a selected
track's tooltip (`trackMapPopupHtml()` in `dashboard.html`) alongside its
Verified/Unverified state, so an operator can see at a glance why a track
is Verified without opening the full details panel.

### Risk score

`Track.risk_score` (`app/risk.py`, computed fresh on every read, never
stored) is a plain, fully-documented point score: `+4` for `drone`
classification (`+2` unknown, `+1` aircraft, `0` bird/friendly), `+2` if
Verified, the worst currently-open incident's severity (`+1` to `+4`) if
any, plus `+1`/`+2` for proximity to the nearest active restricted zone
(`app.zones.nearest_restricted_zone_distance_m`, within 500m/100m of its
centroid) -- the same centroid-distance concept the "Nearest zone" info
row already uses, so the two never disagree about what "distance to
zone" means. This proximity term is what lets a track closing in on a
protected zone score higher *before* it ever actually enters one and a
zone-incursion incident opens (`app.incidents` only fires on actual
entry). The total is capped at 10. An ignored track always scores `0`,
regardless of the rest -- an operator has already said this one
shouldn't compete for attention. This is **not** a claim of a trained ML
risk model (this app ships no such model, same "scaffolding only"
posture as `app/ml/`) -- every point in the score traces back to a field
already shown elsewhere in the UI, so "why is this track ranked here" is
always answerable without a black box. Surfaced on every track card and
as a "Risk: highest first" option in the Sort control (see "Track list:
alert indicator and sort" above -- same within-group-only reordering
rule applies).

### Connection status

The topbar's small dot next to the app name (`#conn-status-dot`,
`updateConnectionChip()`) reflects the dashboard's actual connection
state -- previously a hardcoded, always-green "live" indicator regardless
of whether anything was actually connected. Green ("connected") means
the WebSocket live-push connection (see "Live updates" below) is open;
amber ("degraded") means that push is down but the ordinary HTTP poll is
still succeeding, so the view is still correct, just not instant; red
("unavailable") means the last poll itself failed. Updated after every
poll and on every WebSocket open/close, so it's never stale by more than
one poll cycle.

### Notification bell

The bell icon in the topbar (`detectAndRecordNotifications()`) is a real,
locally-observed event log, not a fabricated feed and not persisted
server-side: on each poll, the dashboard diffs the incoming tracks/
incidents/sensors against what it saw last time, and only ever adds a
notification for a genuine transition it actually witnessed -- a new
incident opening, a sensor's health getting strictly worse (never a
recovery), or a track going `active` -> `lost`. The very first poll after
a page load only establishes that baseline; it never floods the panel
with everything that already existed before the tab was opened. The
unread badge count clears the moment the panel is opened (read, in this
context, means "seen," not "acted on").

### Coasting tracks

A track that's gone quiet for a while, but hasn't yet been marked `lost`
server-side (`TRACK_STALE_SECONDS`, default 30s), is shown as
**coasting** rather than silently still reading `active` as if its
plotted position were still fresh. The dashboard's own coasting threshold
(`COASTING_THRESHOLD_S`, 15s -- half the server default) is a fixed,
documented client-side approximation, since there's no live-config
endpoint to read the real value from; it only changes what's *displayed*,
never what the server itself considers the track's status to be. A
coasting track's map marker dims, and gets a dashed dead-reckoning line
plus an "estimated position (coasting)" tooltip -- its last known
heading/speed carried forward for however long it's actually been quiet,
distinct from the existing motion vector (which always projects forward
by a fixed look-ahead window from a fresh position, regardless of
staleness). Never plotted as if it were a real report: the estimate is
explicitly labeled, and the live marker at the old position is dimmed to
make clear which one to trust.

### Track details tabs

The details panel's sensor-specific content (previously several
always-stacked sections) is now a tab strip: **Live view** (the MJPEG
feed and read-only PTZ cue, see "Live camera view" and "Slew-to-cue"
below), **Quality** (classification confidence, uncertainty, maneuvering,
trail sparkline), **Signal** (the most recent detection's raw per-sensor
fields), **Radar** (bearing/range polar plot from the nearest sensor),
and **Image** (an on-demand snapshot button). Each tab renders its own
specific "not available" message when a track has nothing for it (no
camera nearby, no signal data yet, ...) rather than hiding the tab
outright or fabricating a placeholder.

### Map imagery vs. tracking data

The map background (dark/road/satellite tiles) always comes from an
external tile CDN -- unlike the vendored Leaflet library itself (see
`static/vendor/`), there's no offline/self-hosted tile source by default.
None of the actual tracking/classification/alerting pipeline depends on
it, so a deployment with no internet access (a real possibility for a
field-sited sensor) still works correctly -- it just has a blank map
background, which used to give no indication of why. Three consecutive
tile load failures now shows a small "Map imagery unavailable" banner
(clearing again once tiles start loading) so that's never mistaken for
the dashboard itself being broken.

### Sensor positions on the map

Every registered sensor position (`PUT /api/sensor-registrations/{id}`,
see "Georeferencing" above) shows up on the map as its own small square
marker -- not just listed in the Sensor Health side panel -- colored by
that sensor's real health status (online/stale/offline/missing, the same
signal `GET /api/sensors` already computes) so a sensor that's gone
quiet is visible at a glance on the map itself, not buried in a side
panel you have to go looking in. A sensor that's never been registered
(most GPS-tagged sensors -- cameras, ADS-B receivers -- never need to be)
simply isn't plotted, rather than guessing a position for it. Toggle
with the **Sensors** checkbox alongside Trails/Vectors/Uncertainty.

**"Missing" status**: a sensor with a registered position but zero
detections ever shows a distinct `missing` status (violet), not just
absence from the list -- `app.sensors.get_sensor_health` cross-references
`sensor_registry` for exactly this. Without it, "the sensor nobody
bothered to wire up yet" and "the sensor that just went down" were
indistinguishable: both were simply absent from `GET /api/sensors`,
whether the registration was five minutes or five months old. A
deactivated registration is never flagged this way -- a deliberately
decommissioned sensor isn't a gap to surface.

The Sensor Health panel's own table shows the same registered position
(or "not registered" for a sensor that's only ever reported detections
and has no fixed mount position on file) alongside its health status --
the map plots it, the table now also states it in plain lat/lon.

Two smaller additions alongside it: a scale bar (bottom-right, next to
the zoom controls) for real distance context, and a "fit all" control
(the small square-corners icon above the scale bar) that re-centers the
map on every visible track, zone, and sensor on demand -- useful after
panning away, since the automatic fit-to-bounds only ever runs once, the
first time anything appears, by design (an operator actively looking at
one area shouldn't have the view yanked out from under them every time a
new detection arrives elsewhere).

### Live updates

`GET /ws/live` (WebSocket) pushes a `{"type": "track_update", ...}` or
`{"type": "incident_opened", ...}` event within milliseconds of a
detection landing or an incident opening, instead of the dashboard
waiting for its next poll -- `app/live.py` is the in-process pub/sub
behind it, `app/api/live.py` the endpoint. The dashboard uses it
automatically (`dashboard.html`'s `connectLiveSocket()`); a browser
WebSocket client can't set a custom `X-API-Key` header, so authenticate
via `?api_key=` on the connection URL instead when any key is configured.

Polling isn't removed, just slowed down (`POLL_MS`, 3s -> 15s) and kept
as a fallback -- for two reasons. First, resilience: a push is
best-effort (see `app/live.py`'s `publish()`), so a poll is still what
guarantees the view is eventually correct even if a push is dropped.
Second, and more fundamentally, **a push only reaches whichever replica
the client happens to be connected to** -- this is in-process pub/sub, not
routed through NATS or any other cross-replica bus. Behind a load
balancer fronting multiple replicas (see "Running multiple replicas
behind a load balancer" above), a detection processed by replica B never
pushes to a dashboard client connected to replica A; that client still
gets it, just at the next poll rather than instantly. Single-replica
deployments (the common case) don't have this gap at all.

### Incident reporting

Two different questions, two different endpoints. "How are we doing over
this period" is `GET /api/reports/incidents` -- a rollup (counts by type/
severity/status, resolution-time stats, a per-day trend) over a date
range, aggregated in `app/reporting.py`'s `build_incident_report`, with a
CSV of the underlying incidents at `GET /api/reports/incidents/export`
for a compliance officer who needs the list an aggregate count
summarizes. The dashboard's **Incident Reports** panel (rail icon, or
Alt+5) surfaces this: pick a date range, see the breakdown, or click
"Export CSV".

"What actually happened on this one incident" is
`GET /api/incidents/{id}/report` -- a single incident's full story
(`build_after_action_report`): what was seen, when, by which sensors, the
track's fused classification and final position/speed, any real identity
fragment a sensor actually decoded, and how it was responded to
(acknowledged by whom, resolution time). Every incident in the
dashboard's **Alerts** panel (including resolved ones, not just active
alerts) has a **Report** button that fetches this and renders it as a
printable page (`window.print()`) -- useful for after-action review or an
incident record you want on paper/PDF rather than just on screen.

**Identification section**: `_extract_identification` pulls the most
recent non-null value of any real per-aircraft identity field a sensor
already decoded into a detection's `raw_data` -- DJI DroneID's
`serial_number`, ASTERIX radar's Mode S `aircraft_address`/`callsign`/
squawk (`mode3a`), ASTM F3411 Remote ID's `operator_id` -- into the
report. These were already captured (see the adapter modules named for
each), just not previously surfaced anywhere in the report. Deliberately
**not** a manufacturer/model name: this app has no database mapping
serial/address ranges to manufacturers, so it never fabricates one --
just the raw identifying value an operator or investigator would look up
themselves. The track's `aircraft_category` (real ICAO ADS-B emitter
category, e.g. "Rotorcraft") is included in the Track section when known,
for the same reason.

An open incident's card also has **Acknowledge** and **Resolve** buttons
(driving `POST /api/incidents/{id}/acknowledge` and `.../resolve` above);
an acknowledged one keeps just **Resolve**, for a deliberate operator
judgment call -- "we reviewed this and it's handled."

A red **ALERT** banner appears across the topbar whenever at least one
incident is still open (unacknowledged), naming the count and the worst
severity among them -- clicking it opens the Alerts panel directly. It
disappears again as soon as every open incident has been acknowledged or
resolved; an acknowledged-but-not-yet-resolved incident no longer needs
to interrupt the whole screen (it still counts toward the rail icon's own
badge, a separate, quieter signal).

The system also auto-closes an incident once its own trigger condition
is confirmed gone, so it never sits open/acknowledged indefinitely after
the fact that caused it no longer holds: a zone-incursion incident closes
as soon as a later detection places the track outside that zone again
(`app.incidents.check_zone_incident_resolutions`, run on every detection
alongside the check that opens one), and closing a track (active -> lost
-> closed once it's gone quiet too long, see `app.tracking
.expire_stale_tracks`) closes every incident type still open for it
(`close_incidents_for_closed_track`) -- there'd otherwise be no signal
left to resolve a zone-based one against, and nothing was tracking a
behavioral one (loitering/formation/shadowing) either. An auto-closed
incident is never mistaken for a reviewed one: its description is
suffixed `(auto-closed: ...)` with the reason, and `acknowledged_by`
is left exactly as it was (`null` if no operator ever acknowledged it) --
never fabricated as if someone signed off on it.

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
picked up automatically on startup via a lightweight migration check --
no manual step, no separate tool, for that class of change.

Every SQLite connection uses **WAL** (write-ahead logging) instead of
SQLite's default rollback-journal mode (`app.db._configure_sqlite_connection`)
-- a reader (`GET /api/tracks`, polled every few seconds by every connected
dashboard) no longer blocks behind a writer (a continuously-ingesting
`POST /api/detections`) or vice versa; the two only briefly contend at the
moment a writer actually commits, not for its whole transaction. Not
applicable to PostgreSQL, which handles concurrent readers/writers with
MVCC regardless.

**Alembic** (`migrations/`) is available for the migrations that
startup-time approach structurally can't do safely -- renaming/dropping a
column, changing a column's type, or a real data migration. It's optional
tooling for that specific case, not a required deploy step: see
`migrations/README.md`.

### Backup and restore

Everything durable lives in the one database (`DRONE_DATABASE_URL`) --
there's no other persistent state to back up separately (config is
environment variables; zone seed data is re-loaded from
`app/zones.seed.json` -- or `DRONE_ZONES_SEED_PATH` -- on startup if
present, not written back to). The
`track_kalman_state` table is the one exception worth knowing about: it's
derived, in-flight filter state, not audit data (see its docstring in
`app/schema.py`) -- losing it just means active tracks reinitialize their
Kalman filter from their next detection instead of continuing smoothly,
not lost history. Fine to include in a normal backup; not worth treating
as critical if a restore predates it.

**Automated, unattended backups**: `scripts/backup.sh` wraps both engines'
safe backup mechanism below behind one command that auto-detects which one
you're running from `DRONE_DATABASE_URL`, writes a timestamped file into
`BACKUP_DIR` (default `./backups`), and prunes down to the last
`BACKUP_KEEP_COUNT` (default 14) so it's safe to run on a schedule without
slowly filling the disk:

```bash
BACKUP_DIR=/mnt/backups BACKUP_KEEP_COUNT=30 scripts/backup.sh
```

`scripts/restore.sh <backup-file>` reverses it (stop the app first -- a
restore while it's running races live writes); it refuses to restore a
SQLite backup over a PostgreSQL `DATABASE_URL` or vice versa, and saves a
`.pre-restore-<timestamp>` safety copy of the SQLite file it's about to
overwrite. For bare-metal/VM deployments,
`deploy/drone-multi-sensor-backup.service` + `.timer` run `scripts/backup.sh`
on a daily systemd timer -- see that file's header for install steps. For
Docker Compose, run it from the host against the same `DRONE_DATABASE_URL`
(or `docker compose exec app scripts/backup.sh` with `BACKUP_DIR` pointed
at a mounted volume) on a cron schedule -- there's no built-in scheduler
inside the `app`/`db` containers themselves.

**SQLite** (the default, `data/drone_sensor.db`): a plain file, but don't
`cp` it while the app is running -- SQLite's WAL/journal files can leave a
naive file copy in an inconsistent state. `scripts/backup.sh` above uses
Python's `sqlite3.Connection.backup()` API for this reason (not a file
copy, and not a dependency on the separate `sqlite3` CLI package, which
isn't installed in this app's own Docker image); the equivalent by hand,
if you have the CLI installed:

```bash
sqlite3 data/drone_sensor.db ".backup data/drone_sensor.backup.db"
# or, equivalently, from any DB client already connected to it:
sqlite3 data/drone_sensor.db "VACUUM INTO 'data/drone_sensor.backup.db'"
```

Restore by stopping the app and replacing the live file with the backup
(or pointing `DRONE_DB_PATH`/`DRONE_DATABASE_URL` at the backup file
directly to restore into a fresh location instead of overwriting).

**PostgreSQL**: standard `pg_dump`/`pg_restore`, no app-specific wrinkle --
the schema is plain tables via SQLAlchemy Core, nothing PostgreSQL-only
(no stored procedures, no extensions) for `pg_dump` to miss.

```bash
pg_dump -Fc "$DRONE_DATABASE_URL" > drone_sensor_backup.dump
# restore into a fresh (or freshly-dropped-and-recreated) database:
pg_restore -d "$DRONE_DATABASE_URL" --clean --if-exists drone_sensor_backup.dump
```

Neither path has been exercised against a large production-scale database
in this environment -- `pg_dump`/`sqlite3 .backup` are the standard,
well-tested tools for each engine, but restore time at real data volumes
depends on retention settings (`DRONE_DETECTION_RETENTION_DAYS`) and
deployment-specific data volume, not something this repo can benchmark for
you.

## Sensor realism

**Getting your first real sensor talking to this**: before wiring any
adapter below into the API, verify the sensor hardware itself works in
isolation -- it isolates hardware/driver problems from application-layer
ones. For the cheapest and most common starting point, an RTL-SDR dongle
doing ADS-B reception:

1. If running on WSL2 rather than native Linux, the dongle needs USB
   passthrough first (WSL2 has no native USB access):
   ```powershell
   # Windows side (PowerShell, as Administrator):
   usbipd list                      # find the RTL-SDR's BUSID
   usbipd bind --busid <BUSID>
   usbipd attach --wsl --busid <BUSID>
   ```
   ```bash
   # WSL side -- confirm it's visible before going further:
   lsusb   # expect "Realtek Semiconductor Corp. RTL2838 DVB-T"
   ```
   On a Raspberry Pi or other native Linux host, the dongle is already a
   normal USB device -- skip straight to step 2.
2. Install and run a real ADS-B decoder -- this project's own
   `dump1090_bridge.py` (below) is a *bridge*, not a decoder; it consumes
   dump1090's SBS-1 output rather than talking to the SDR directly:
   ```bash
   sudo apt install -y dump1090-fa   # FlightAware's maintained fork
   dump1090-fa --device-index 0 --net --net-sbs-port 30003
   ```
3. Check `http://<host>:8080` (dump1090-fa's own built-in map). If
   aircraft are within range, blips should appear within a minute or two.
   This step alone confirms the dongle, antenna, and placement are working
   correctly, before this tracker enters the picture at all.
4. Only once step 3 shows real traffic, point this app's bridge at
   dump1090 as described below.

The same "confirm the raw sensor/decoder output first, then bridge it in"
order applies to every adapter in this section -- a radar's ASTERIX feed,
a camera's RTSP stream, a Bluetooth Remote ID scan -- since a bridge
script can't distinguish "no detections because the sky is empty" from
"no detections because the upstream feed is misconfigured."

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

`range_m` from an azimuth/range sensor is *slant range* -- straight-line
distance to the target, not horizontal ground distance -- since that's
what radars actually report (e.g. ASTERIX CAT048's RHO field, see
`app/adapters/asterix.py`). Georeferencing corrects this to ground range
using the target's and sensor's altitude difference whenever both are
known (`app/geo.py`'s `slant_range_to_ground_range_m`), which matters most
at close range/steep look angles -- a target 150m above a radar at 500m
slant range is really only ~477m away over the ground, a ~5% position
error left uncorrected. Falls back to using `range_m` unadjusted when the
detection carries no altitude of its own (bearing-only acoustic arrays,
many RF direction finders), same as before this correction existed.

**ADS-B via dump1090** (`app/adapters/`): the most common real-world way
to get ADS-B into this tracker is an RTL-SDR dongle (~$20) running
[dump1090](https://github.com/flightaware/dump1090) or similar, which
serves live traffic on TCP port 30003 in the SBS-1/BaseStation text
format. A small bridge script parses that feed and posts it to the API:

```bash
.venv/bin/python -m app.adapters.dump1090_bridge --sbs-host 127.0.0.1 --sbs-port 30003
```

**Distinct map symbols per aircraft type**: the SBS-1 text feed above only
carries position/altitude, not aircraft type -- but dump1090-fa (and
similar forks) also serve a richer `aircraft.json` on their web UI port
(default 8080) that includes the real ICAO ADS-B *emitter category*
(DO-260B Table 2-36: light/heavy fixed-wing, rotorcraft, glider,
lighter-than-air, UAV, ...). The bridge polls this automatically
(`--aircraft-json-url` to override its location, `--no-category-lookup`
to disable) and merges the category into each detection; `app.tracking`
carries it onto the track (`Track.aircraft_category`), and the dashboard
map renders a genuinely different symbol per category group -- a rotor
cross for rotorcraft, a wing bowtie for gliders, a balloon envelope for
lighter-than-air, a diamond for UAVs, a realistic airplane silhouette
(the same one the sidebar/details thumbnail already uses) for fixed-wing
aircraft -- instead of one generic shape for every aircraft. Falls back
to that same silhouette whenever no category is known (most GA aircraft
with older transponders never report one), never a guess. If
`aircraft.json` isn't reachable at all (a minimal dump1090 install
without its web server running), detections keep flowing normally, just
without category enrichment.

An aircraft-classified marker is also colored by **altitude** (real,
already-tracked `Track.altitude_m` data), low (orange) to high (purple)
-- the same convention most real flight trackers (FlightRadar24, OpenSky)
use, with a gradient swatch in the map legend. Every other classification
(drone/bird/friendly/unknown) stays colored by *what kind of object it
is*, not altitude -- for a counter-drone system, that's the more
operationally important signal to color by at a glance, and altitude
coloring would wash it out. Falls back to the flat classification color
when altitude isn't known, same "never guess" rule as the category
symbols above.

Drone and unknown tracks likewise get a real symbol on the map, not a
plain dot -- the same quadcopter glyph (four rotors and a body) already
used for the sidebar/details thumbnail, so a drone reads as a drone at a
glance instead of just a colored circle.

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
a `camera` detection whenever it sees motion above a threshold. Like every
other detection-posting adapter in this package, it's built on
`app/adapters/sdk.py` -- the shared POST-to-`/api/detections` +
`--api-url`/`--api-key`/`--sensor-id` argparse wiring that used to be
duplicated independently in each one. New adapters should use it directly
rather than re-copying the pattern; it also adds `--max-retries`/
`--retry-backoff` for a transient network blip, and a `format_post_error`
helper that surfaces the server's actual JSON error detail (e.g. a
misconfigured `--api-key`) instead of just an HTTP status line -- neither
of which any hand-rolled adapter had before.

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

## Live camera view

Both camera adapters above only ever post a *detection* -- motion, or an
object class -- never the actual image. `GET /api/sensors/{sensor_id}/live`
(`app/api/camera_live.py`) is a separate, real live view: it opens a
registered camera's RTSP/HTTP stream server-side and re-proxies it to the
browser as an MJPEG feed (a `multipart/x-mixed-replace` response any
`<img>` tag can render directly, no video player needed), so the
dashboard's track details panel can show what a nearby camera currently
sees -- throttled to 8 FPS (`_MAX_FPS` in that module) since this is a
"confirm what the camera sees right now" live view, not a claim of smooth
broadcast-quality video.

Register the camera's stream URL alongside its position:

```bash
curl -X PUT http://127.0.0.1:8000/api/sensor-registrations/cam-1 \
  -H "Content-Type: application/json" \
  -d '{"sensor_type":"camera","latitude":51.50,"longitude":-0.10,
       "camera_stream_url":"rtsp://192.168.1.50/stream1"}'
```

`camera_stream_url` is write-only in practice: `GET /api/sensor-registrations`
always reports it as `null` regardless of what's actually stored, since a
camera's stream URL commonly embeds its own login credentials, and
there's no legitimate reason a dashboard viewer needs it back once it's
set -- only the live-view endpoint itself, server-side, does. The
dashboard picks whichever registered camera is nearest a selected track
and streams from it automatically; no separate configuration in the UI
itself.

Requires the same camera extras as the adapters above
(`pip install -r requirements-camera.txt`) -- `opencv-python-headless` is
what actually opens the RTSP stream and encodes each frame as JPEG.
Without it, the endpoint returns `501` with that exact instruction rather
than an empty/broken stream.

If a *second*, genuinely distinct camera sensor is also registered near
the same track, the dashboard shows it as a small inset thumbnail in the
corner of the main live view -- never a duplicate of the main feed, and
never shown at all when there's only one camera nearby.

`GET /api/sensors/{sensor_id}/snapshot` is the same idea for a single
still frame instead of a feed: it opens the stream fresh, grabs the first
frame that decodes, and returns it as one JPEG (`502` if the camera never
produces a decodable frame within 10s). Not a stored-image history --
there's no capture pipeline or table behind it -- just "what does this
camera see right now," as a still. Surfaced as a "Take snapshot" button
under the dashboard's Image tab.

## Downstream C2 integration

**Publishing tracks to Anduril Lattice** (`app/adapters/lattice_bridge.py`):
unlike every other module in `app/adapters/`, which bring a sensor's raw
signal IN as detections, this pushes this tracker's own fused output OUT --
polling `GET /api/tracks?status=active` and publishing each as a Lattice
[Entity](https://developer.anduril.com/reference/rest/entities) via the
[Lattice SDK](https://pypi.org/project/anduril-lattice-sdk/), so this app's
tracker can appear as one more contributing source on Lattice's common
operating picture alongside a deployment's other sensors and assets. The
field mapping (`app/adapters/lattice.py`) is pure and unit-tested without
the SDK installed, the same split as `app/adapters/mavlink.py` vs
`mavlink_bridge.py`.

```bash
pip install -r requirements-lattice.txt
export LATTICE_ENDPOINT=lattice-your_env_id.env.sandboxes.developer.anduril.com
export LATTICE_CLIENT_ID=...
export LATTICE_CLIENT_SECRET=...
export SANDBOXES_TOKEN=...          # only needed for Lattice Sandboxes
.venv/bin/python -m app.adapters.lattice_bridge --api-url http://127.0.0.1:8000/api
```

Classification maps to Lattice's `mil_view.disposition` conservatively:
`friendly` -> `DISPOSITION_FRIENDLY`, `drone`/unknown aerial contacts ->
`DISPOSITION_SUSPICIOUS` (Lattice's "warrants attention" bucket), `bird`/
`aircraft` -> `DISPOSITION_NEUTRAL`. Never `DISPOSITION_HOSTILE` -- this app
makes no intent judgment, only a classification one, and mapping to HOSTILE
would overclaim what it actually knows.

With `--attach-thumbnails`, each published entity also gets a snapshot image
via the [Objects API](https://developer.anduril.com/reference/rest/objects)
(the same upload-then-`override_entity` pattern as Anduril's own
`sample-app-thumbnail`), sourced from the track's most recent camera
detection -- requires running `camera_yolo.py` with `--snapshot-dir` set, off
by default so no deployment pays for snapshot disk I/O it doesn't use.

**What this deliberately does NOT do**: publish tracks one-way only, no
tasking. Anduril's own sample apps also demonstrate commanding a Lattice
Asset (e.g. an `Orbit` task sending a vehicle to investigate a suspicious
track) -- that's a genuinely different problem domain this app doesn't
model. This tracker fuses sensor detections into tracks; it has no concept
of a commandable asset, an autonomy stack, or a tasking protocol to send
one. Bolting on a fake "Orbit" call with no real asset behind it would be
scope creep dressed up as a feature, not a real integration -- if you need
Lattice-driven tasking, that logic belongs in whatever system actually
operates your assets, consuming this tracker's published Entities as one of
its own inputs. `samples/lattice/orbit_task/` demonstrates that pattern
as an explicitly-standalone, explicitly-a-simulation sample instead --
see `samples/lattice/README.md`.

**Other Lattice sample apps** (`samples/lattice/`, matching [Anduril's own
sample apps](https://developer.anduril.com)): an Objects API CLI, an
entity visualizer (a map of everything in a Lattice environment, not just
this tracker's own tracks), and a maritime AIS-to-Lattice publisher --
each independent of this repo's own tracking pipeline. See that
directory's own README for details.

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
Wi-Fi-based FPV/control links, and long-range RC control links
(ExpressLRS/TBS Crossfire-style, at both 900 MHz and 2.4 GHz) -- instead
of trusting a single flat RF confidence number. The 900 MHz entry in
particular closes a real coverage gap: every other signature sits in the
2.4/5.8 GHz bands, so a sub-1 GHz control link (a real, common band for
long-range FPV) previously matched nothing at all. This mirrors how real
counter-drone RF sensors actually
work: classifying frequency band, channel bandwidth, and hopping behavior
against known signature libraries for common link types, not decoding
encrypted proprietary protocol content. A signature match's confidence is
used (via `app/fusion.py`) whenever it's higher than the sensor's own
reported confidence, so a low-confidence RF detection with a
signal-shape that matches a known drone link still classifies as `drone`.
It cannot decode a link's payload, extract telemetry, or identify a
specific aircraft -- only that its RF envelope is consistent with a known
type of control/video link. The band/bandwidth windows in
`BUILT_IN_SIGNATURES` are drawn from published consumer/hobbyist RF
specifications and are approximate, by protocol family -- not exact
per-model specs. That's a deliberate limit, not an oversight: getting
real per-model fidelity (telling a DJI Mavic 3 apart from a Mini 4 Pro by
RF envelope alone) needs a verified signal-sample dataset or a licensed
signature library, and this project has no access to one -- a
confidently-specific-looking but unverified number would be worse than
an honestly approximate one in a system people make security decisions
from.

**Plugging in real signatures**: point `DRONE_RF_SIGNATURES_PATH` at a
JSON file of your own -- real captured/verified RF samples, a licensed
signature library, or FCC ID equipment-authorization filings you've
looked up yourself -- and `app/rf_signatures.py` loads them via
`load_operator_signatures()`, tried *before* the built-in family-level
table, so a verified per-model entry wins over the approximate fallback
for the same frequency/bandwidth window:

```json
[
  {
    "name": "my_verified_model",
    "label": "Example Drone Model X1",
    "freq_bands_mhz": [[2400.0, 2483.5]],
    "bandwidth_mhz": [9.0, 11.0],
    "frequency_hopping": true,
    "drone_link_confidence": 0.97,
    "source": "field capture 2026-01-01, verified against known unit S/N ..."
  }
]
```

`source` is free text (shown in logs when a signature loads) so a
reviewer can tell an approximate built-in from a verified operator entry
at a glance -- always fill it in with where the numbers actually came
from, not a placeholder.

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

## Generic RF energy-detection sweep

Every RF adapter above only sees a transmission it already knows how to
decode (DJI OcuSync, MAVLink, ASTM F3411). `app/adapters/rf_sweep_bridge.py`
is the odd one out: it doesn't decode anything, it just flags any
frequency bin whose power exceeds the sweep's own noise floor by
`--threshold-db` -- the same "energy detection" first pass real
counter-drone RF systems run before any signal classification, useful for
catching an unknown or non-cooperative emitter none of the protocol-
specific adapters above would recognize.

It reads [`hackrf_sweep`](https://github.com/greatscottgadgets/hackrf)'s
CSV output from stdin (same stdin-piped shape as `dji_droneid_bridge.py`
above -- no SDR dependency in this process itself):

```bash
# apt install hackrf, or build hackrf-tools from the repo above
hackrf_sweep -f 2400:2500,5725:5875 -w 600000 \
    | .venv/bin/python -m app.adapters.rf_sweep_bridge \
        --sensor-id rf-sweep-1 --target-lat 51.50 --target-lon -0.10
```

**No protocol identification, no direction, no range.** A flagged bin
could be a drone control link, a WiFi AP, a microwave oven, or a cordless
phone -- this can only tell you *something* is transmitting there above
the noise floor, not what. And an omnidirectional SDR sweep has no
bearing or range at all, so every detection is reported at the sensor's
own `--target-lat`/`--target-lon`, the same honest compromise
`camera_motion.py` makes for a monocular camera's identical limitation.
Best used as a coarse alert that a human or a more specific sensor then
investigates, not as a standalone drone/not-drone classifier.

The noise floor is each sweep's own median power by default -- robust to
a handful of genuinely occupied bins, but a site with persistent in-band
RF activity (a permanently-on WiFi AP, say) would skew it. Record a
quiet-band baseline first and point `--baseline-csv` at it for a fixed
reference instead:

```bash
hackrf_sweep -f 2400:2500,5725:5875 -w 600000 > control.csv   # ~1 minute, no drone present
.venv/bin/python -m app.adapters.rf_sweep_bridge --baseline-csv control.csv \
    --sensor-id rf-sweep-1 --target-lat 51.50 --target-lon -0.10
```

`hackrf_sweep`'s CSV line format (`date, time, hz_low, hz_high,
hz_bin_width, num_samples, dB, dB, dB, ...`, with a single sweep split
across several lines sharing one timestamp) was verified against
`hackrf_sweep`'s actual documented output and
[`tesorrells/RF-Drone-Detection`](https://github.com/tesorrells/RF-Drone-Detection)'s
reference parsing of it, not guessed from a byte-offset diagram.

## Real ASTM F3411 Remote ID reception

`app/remote_id.py` (see the Classification fusion section below) is a
signed-claim scheme this app defines -- verifying a cryptographic
assertion, not anything a real drone actually broadcasts. Every drone
over 250g sold in the US/EU is now separately required to broadcast real
**ASTM F3411 Remote ID** over Bluetooth or Wi-Fi, and this app can receive
either transport:

```bash
pip install -r requirements-remoteid.txt

# Bluetooth Low Energy -- any standard Bluetooth adapter works, no SDR needed:
sudo .venv/bin/python -m app.adapters.astm_remote_id_ble_bridge --sensor-id remote-id-1

# WiFi Beacon -- needs a monitor-mode-capable WiFi adapter already switched
# into monitor mode on the target channel (see the module's own docstring
# for the iw/ip commands to set that up):
sudo .venv/bin/python -m app.adapters.astm_remote_id_wifi_bridge \
    --interface wlan0mon --sensor-id remote-id-wifi-1
```

Remote ID's whole design point is that anyone can passively receive it --
neither transport needs the drone's cooperation beyond broadcasting what
the standard already requires it to. Decoding uses
[`dtpyodid`](https://github.com/dronetag/python-odid), a real Python
implementation of the ASTM F3411 message formats from Dronetag (a
commercial Remote ID hardware vendor), verified here by round-tripping
real messages through the library's own encoder/decoder. The Bluetooth
framing was cross-checked against `opendroneid/transmitter-linux`'s
reference implementation, and the WiFi Beacon vendor-specific element
layout (the 3-byte ASD-STAN OUI, application code, and message-counter
byte preceding the actual message-pack bytes) against
`opendroneid/opendroneid-core-c`'s reference C implementation -- neither
is a byte-offset parser guessed from memory. Both transports send one
message per broadcast (position, operator ID, serial number, ...),
cycling through them, so both bridges accumulate a device's state across
several broadcasts before there's enough to post a detection --
`app/adapters/astm_remote_id.py` is the transport-independent logic
(message-field extraction, state accumulation, payload building) both
bridges share.

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

The dashboard's Live view tab shows this same cue (pan/tilt/distance) as a
**read-only** display next to the nearest registered camera -- it calls
the cue endpoint above and renders the numbers it gets back, but never
sends a command to any camera itself; driving real hardware is still only
`onvif_ptz_bridge.py`'s job, run separately.

## Airspace data

By default, zones come from the single hand-seeded polygon in
`app/zones.seed.json` -- fine for a demo, not for representing real
airspace. Three ways to add real zones, in increasing order of how much
setup they need:

**The dashboard, at runtime** -- click the zones icon in the rail, "+ New
zone", then click points on the map (3 minimum) to draw a polygon, fill
in name/type/altitude band, and save. Editing an existing zone
(re-drawing its polygon, changing its type, deactivating it) works the
same way via each zone's "Edit" button. Requires an admin-role key (see
"Security & access control" below) -- this is the operational path, no
file edits or restart needed.

**`POST`/`PUT /api/zones`** directly, admin-only, if you're scripting zone
setup rather than clicking through the UI:

```bash
curl -X POST http://127.0.0.1:8000/api/zones \
  -H "X-API-Key: $ADMIN_KEY" -H "Content-Type: application/json" \
  -d '{"name": "warehouse-perimeter", "zone_type": "restricted",
       "polygon": [[51.49, -0.11], [51.49, -0.09], [51.51, -0.09], [51.51, -0.11]]}'
```

A zone's name is unique per site (not globally -- two different sites may
each reasonably have their own "Restricted Zone"), enforced at the
database layer (`idx_zone_site_id_name`, `app/schema.py`), not just as an
application-level check -- two concurrent `POST /api/zones` requests for
the same new name can't both succeed.

**`app/zones.seed.json`** (or `DRONE_ZONES_SEED_PATH`), loaded at every
startup -- still the right place for a zone that should exist by default
in every fresh deployment (what ships with this repo), not for zones an
operator adds afterward; those belong in the database via the two options
above, not in a file a deploy might overwrite.

A hand-edited seed file has no validation beyond Pydantic's field-level
checks (each vertex is a float pair) -- a self-intersecting polygon
doesn't error, `app.zones.point_in_polygon`'s ray-casting test just
silently gives a wrong inside/outside answer near the crossing.
`app/adapters/validate_zone.py` checks the actual geometry (at least 3
vertices, real lat/lon range, no self-intersection) before a polygon goes
in:

```bash
# Check a new polygon, or append it once it's valid:
.venv/bin/python -m app.adapters.validate_zone check --polygon '[[51.49,-0.11],[51.49,-0.09],[51.51,-0.09],[51.51,-0.11]]'
.venv/bin/python -m app.adapters.validate_zone add --name "New Zone" --zone-type restricted \
  --polygon '[[51.49,-0.11],[51.49,-0.09],[51.51,-0.09],[51.51,-0.11]]' --write app/zones.seed.json

# Re-validate everything already in a seed file (catches a polygon that
# was hand-edited badly after the fact):
.venv/bin/python -m app.adapters.validate_zone check-file app/zones.seed.json
```

Four real, publicly published FAA data sources can supplement or
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

**FAA Class Airspace** (`app/airspace/faa_class_airspace.py`): the
permanent Class B/C/D/E controlled-airspace surface areas drawn on every
VFR sectional chart -- a different, longer-lived kind of restriction than
either the facility map's altitude ceilings or a NOTAM's temporary one.
Same unauthenticated ArcGIS FeatureServer mechanics as the facility map
above (same FAA ArcGIS org), via the same import CLI with `--source
class-airspace`:

```bash
.venv/bin/python -m app.adapters.faa_zones_import --source class-airspace \
  --min-lon -0.5 --min-lat 51.3 --max-lon 0.3 --max-lat 51.7
```

Each surface area is imported as a `monitoring` zone (the same reasoning
as the facility map's: entering Class B/C/D/E means real-world ATC
authorization is needed, not that an intrusion just happened), named with
its class and airport identifier and carrying its real floor/ceiling
(`SFC` -- surface -- becomes a real `min_altitude_m` of `0`, `UNLTD`
becomes no `max_altitude_m` cap at all, not a fabricated number). The
field names (`CLASS`, `LOWER_VAL`/`LOWER_UOM`/`LOWER_CODE`,
`UPPER_VAL`/`UPPER_UOM`/`UPPER_CODE`, ...) are confirmed from the FAA's
own published AIS Open Data Dictionary, but -- like the facility map
above -- outbound access to every FAA/ArcGIS domain (including the Data
Dictionary PDF itself and the FeatureServer) wasn't available from the
environment this was built in, so `DEFAULT_FEATURE_SERVER_URL` is
inferred from the same org/naming convention the facility map's already-
confirmed URL uses, not itself confirmed by a live request. Sanity-check
your first real import against a known Class B/C/D airport's published
airspace, and pass your own `--feature-server-url` if it's moved.

**FAA Special Use Airspace** (`app/airspace/faa_special_use_airspace.py`):
Prohibited, Restricted, Warning, Alert, Military Operations, and National
Security Areas -- the most directly relevant of these four sources for a
drone-detection deployment specifically, since Prohibited/Restricted
areas are genuinely not permitted to fly in without specific clearance
(P-56 over the White House/Capitol is the textbook example), not just
"needs ATC coordination" the way Class Airspace above is. Same
unauthenticated ArcGIS FeatureServer mechanics, via `--source
special-use`:

```bash
.venv/bin/python -m app.adapters.faa_zones_import --source special-use \
  --min-lon -0.5 --min-lat 51.3 --max-lon 0.3 --max-lat 51.7
```

Prohibited and Restricted areas import as `no_fly` zones (this app's
closest match to "genuinely not permitted"); Warning/Alert/MOA/National
Security Areas import as `monitoring`, the same "be aware, not an
automatic intrusion" reasoning as the other two sources above. The
classification is deliberately **not** a trust in one field's exact,
unconfirmed spelling: it keys off the well-known chart designator prefix
on the area's own name (`P-`/`R-` for Prohibited/Restricted, per the
same convention every sectional chart and aviation reference uses),
falling back to a loose match on the `TYPE` field only when the name
doesn't start with a recognized prefix. Same caveat as the other two
sources -- outbound access to every FAA/ArcGIS domain wasn't available
to validate a live response, so sanity-check your first real import
against a known Prohibited or Restricted area before relying on this for
anything safety-relevant.

**FAA NOTAMs** (`app/airspace/faa_notam.py`, `app/adapters/faa_notam_check.py`):
Notices to Air Missions cover the kind of temporary/event-driven airspace
restriction a static zone file or the facility map's fixed grid can't --
TFRs, a stadium event, a UAS area closed for the day. Requires a free
`client_id`/`client_secret` from the [api.faa.gov](https://api.faa.gov)
developer portal:

```bash
# Print only, for situational awareness:
.venv/bin/python -m app.adapters.faa_notam_check \
  --client-id "$DRONE_FAA_NOTAM_CLIENT_ID" --client-secret "$DRONE_FAA_NOTAM_CLIENT_SECRET" \
  --lat 51.5 --lon -0.1 --radius-nm 50

# Also import each as a zone into site 1 -- safe to re-run periodically
# (e.g. from cron): a NOTAM still active from a previous run is skipped,
# not duplicated.
.venv/bin/python -m app.adapters.faa_notam_check \
  --client-id "$DRONE_FAA_NOTAM_CLIENT_ID" --client-secret "$DRONE_FAA_NOTAM_CLIENT_SECRET" \
  --lat 51.5 --lon -0.1 --radius-nm 50 --site-id 1
```

Each imported NOTAM becomes a `restricted` zone shaped as the **search
circle actually queried to find it** (`center_lat`/`center_lon`/`radius_nm`,
turned into a 16-vertex polygon), not a guess at that NOTAM's own real
footprint: NOTAM geometry, when present at all, isn't reliably a clean
polygon the way the facility map's is, and a wrong guessed shape is worse
than no zone. Every NOTAM found in one fetch shares the identical circle
-- what distinguishes them is the zone name (`NOTAM <number> (<ICAO
location>)`), not the shape. This is a deliberate, honest approximation
("something is active somewhere within this circle"), not the NOTAM's
precise boundary -- for any NOTAM whose real geometry you actually know
(e.g. parsed from its raw text), create that zone by hand via
`POST /api/zones` instead of relying on this one.

**This one genuinely hasn't been validated against a live account** --
both `api.faa.gov` and its developer-registration flow were unreachable
from this environment's network, so unlike every other real-protocol
integration in this README, the request/response shape here is
documented-but-unverified; treat it as a starting point to confirm against
your own registered account, not a proven integration.

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

**An operator's deliberate override, distinct from the automated vote
above**: `POST /api/tracks/{id}/classify` (body `{"classification": "friendly"}`,
`{"classification": "drone"}`, or `{"classification": "unknown"}`) sets
`Track.classification` directly -- "that's our own authorized drone," "I've
personally confirmed this is hostile," or "never mind, clear my own
earlier call and let the automated vote decide again" -- not another vote
for `app.fusion` to weigh. `classification_confidence` goes to `1.0` for
friendly/drone (nothing left to be uncertain about) and back to `None` for
unknown (reverting an override isn't a confident claim of its own).
Restricted to just those three values (not the full `Classification`
enum): an operator watching the dashboard is making one of three calls,
not reclassifying something as a bird or an aircraft, which is what
sensor evidence itself is for. Surfaced as **Friend**/**Foe**/**Neutral**
buttons in the dashboard's track details panel; audited (`track.classify`)
like every other operator action (`app/db.py`'s `record_audit`). **Foe**
specifically asks for confirmation first (`window.confirm`) before the
call goes through -- it's the one override that can escalate an
incident's severity, worth one deliberate extra step where Friend/Neutral
(which only ever calm things down) don't need it.

**Suppressing alerts without touching classification**: `POST
/api/tracks/{id}/ignore` (body `{"ignored": true}`, optionally
`"duration_minutes": N`) sets `Track.ignored` -- an **Ignore** button in
the dashboard (with a small menu: 5 min / 30 min / Indefinitely),
alongside Friend/Foe/Neutral. An ignored track is never hidden or altered
(it keeps updating, tracking, and showing up in the list -- just dimmed,
with an "Ignored" badge); the only thing that actually changes is that
`app.incidents` never opens a new zone-incursion or behavioral incident
for it (`_open_incident` and `_open_behavioral_incident` both check the
flag first). Toggle back off with `{"ignored": false}` (**Unignore**),
which also clears any expiry.

A timed ignore (`duration_minutes` set) stores a real `Track.ignored_until`
and expires on its own -- no separate sweep job needed: `app.db
._row_to_track`, the one shared hydration point every track read goes
through (the API layer, the tracking pipeline's own incident checks,
everywhere), resolves an expired `ignored_until` back to `ignored=false`
on the spot. "Indefinitely" (the default, `duration_minutes` omitted)
behaves exactly like the original always-permanent Ignore.

**Classification confidence, distinct from the label**: `Track.classification_confidence`
(0-1) is how strongly *current* evidence backs whatever label is actually
stored -- not the winning label, if the two have diverged, since the
upgrade-only rule above means the stored label can outlive contradicting
evidence by design. It can fall even while the label itself never
downgrades: a track marked `drone` early on whose more recent detections
increasingly look like `bird` keeps its `drone` label but shows falling
confidence in it, an honest signal instead of either silently downgrading
or looking exactly as certain as a freshly-reconfirmed track. `GET
/api/tracks`/`GET /api/tracks/{id}` additionally apply read-time
staleness decay on top of the as-of-last-detection value `app/tracking.py`
stores -- a track nobody's heard from in a while decays linearly toward
`DRONE_CLASSIFICATION_CONFIDENCE_FLOOR` (default `0.3`, never fully zero:
real evidence did once support it) over `DRONE_CLASSIFICATION_CONFIDENCE_DECAY_SECONDS`
(default `300`), computed fresh on every request rather than kept current
by a background job. The dashboard's track details panel shows it as a
**Confidence** meter alongside Maneuvering/Uncertainty in Track quality.

**Severity escalates with sensor-type corroboration**: fusing multiple
sensors into one classification (above) doesn't by itself change how
*actionable* an incident is -- a `drone` reading from a single acoustic
sensor previously got the same severity as one independently confirmed by
radar+RF+camera together. `app/incidents.py` now escalates a zone-incursion
or behavioral incident's severity one level (capped at `critical`) once at
least `DRONE_INCIDENT_CORROBORATION_MIN_SENSOR_TYPES` (default `2`)
distinct sensor types have reported on the opening track, using the same
recent-detection window classification fusion itself considers
(`DRONE_FUSION_HISTORY_LIMIT`). The incident's description gets an
`(escalated: corroborated by N sensor types)` suffix when this fires, so
it's visible in the Alerts panel and after-action reports why severity is
higher than the classification alone would suggest.

**Labeling real detections for training**: the missing piece between "no
labeled dataset" and being able to train a real model is real labeled
data -- this doesn't create any, but gives you a way to build it up as
real sensor traffic arrives, rather than hand-editing a CSV. In the
dashboard, select any track and its **Label training data** section shows
its most recent detections with four quick-label buttons (drone/bird/
aircraft/unknown); clicking one calls `PUT /api/detections/{id}/label`,
and clicking the same one again clears it (undoing a mis-click). This is
a human's ground-truth label, stored independently of and never
overwriting the track's own system-derived `classification` -- see
`app/models.py`'s `Detection.human_label` docstring. Once you've labeled
enough real detections, `GET /api/ml/training-data/export` returns them
as a CSV in exactly the shape `app/ml/train.py --csv` expects:

```bash
curl -o labeled.csv http://127.0.0.1:8000/api/ml/training-data/export
python -m app.ml.train --csv labeled.csv --out model.joblib
```

**ML-based classification (optional, `app/ml/`)**: each detection's label
is currently decided by `app/classification.py`'s rule (sensor type +
confidence threshold), not a trained model -- there's no real labeled
drone/bird/aircraft dataset anywhere in this repo to train one from
honestly. What exists instead is real training/inference *scaffolding*,
ready the moment genuine labeled data does exist:

```bash
pip install -r requirements-ml.txt   # scikit-learn -- not needed otherwise
python -m app.ml.train --csv your_labeled_detections.csv --out model.joblib
export DRONE_ML_MODEL_PATH=model.joblib
```

Once configured, `app/fusion.py` consults the trained model as a first
opinion for each detection, falling back to the rule-based classifier when
unconfigured (the default), the model has no opinion, **or the model's own
top-class probability is below `DRONE_ML_CONFIDENCE_THRESHOLD`** (default
`0.6`) -- a genuinely uncertain prediction (e.g. 0.3 for the winning class
in a 4-class problem, barely better than a coin flip) doesn't get to
silently override well-tested rule-based logic just because *some* model
file happens to be configured. `app/ml/train.py`'s own docstring documents
the expected CSV columns. **Only ever point `DRONE_ML_MODEL_PATH` at a
model file you trained yourself or otherwise fully trust** -- loading a
model file deserializes it via `joblib` (pickle under the hood), which can
execute arbitrary code for a maliciously crafted file, the same risk class
as unpickling any other untrusted data.

Training itself (`python -m app.ml.train`) does more than fit-and-save:
it reports `k`-fold cross-validation accuracy alongside the single
held-out split (a single split's accuracy is noisy, especially on the
small datasets a first real labeled set is likely to be), prints feature
importances so you can see what the model actually learned, trains with
`class_weight="balanced"` (real labeled detections won't arrive evenly
split across drone/bird/aircraft/unknown -- without this a classifier can
score deceptively well on accuracy alone by mostly predicting whichever
class is most common), and rejects any label with fewer than 2 rows with
a clear error up front rather than crashing deep inside scikit-learn.
Feature extraction (`app/ml/features.py`) also derives an
`rf_signature_match_confidence` feature from the same known-drone-
control-link RF envelope matching (`app/rf_signatures.py`) that
`app/fusion.py` already uses to boost confidence for a rule-based RF
classification -- real, already-computed domain signal, not anything
fabricated, given to the model as an additional feature to learn from.

**Acoustic classification (optional, `app/ml/train_acoustic.py` +
`app/ml/acoustic_model.py`)**: a separate, parallel piece of scaffolding
for `app/adapters/acoustic_array_bridge.py`, which otherwise reports a
single flat, manually-estimated `--confidence` for every detection --
no classification of the actual rotor/propeller acoustic signature, only
beamforming's real bearing estimate. `app/acoustic_features.py` extracts
MFCCs (Mel-Frequency Cepstral Coefficients, the standard spectral
features for this) from a recorded audio block via textbook DSP (framing
+ Hamming window -> power spectrum -> triangular mel filterbank -> log ->
DCT-II) -- pure numpy, no new dependency for that step:

```bash
pip install -r requirements-ml.txt   # same scikit-learn/joblib as above

# One subdirectory per label, each full of your own labeled .wav
# recordings -- same trainable label set as app.ml.train (not "friendly"):
#   data/acoustic/drone/*.wav
#   data/acoustic/bird/*.wav
#   data/acoustic/aircraft/*.wav
#   data/acoustic/unknown/*.wav
python -m app.ml.train_acoustic --data-dir data/acoustic --out acoustic_model.joblib
export DRONE_ACOUSTIC_ML_MODEL_PATH=acoustic_model.joblib
```

Once configured, `acoustic_array_bridge.py`'s `watch()` consults the
trained model for each recorded block, using its predicted DRONE-class
probability as `--confidence` instead of the manual value -- falling back
to `--confidence` exactly as before whenever the model has no opinion (the
same "no opinion below `DRONE_ACOUSTIC_ML_CONFIDENCE_THRESHOLD`" gating
`app/ml/model.py` already uses, default `0.6`) or isn't configured at all.
Every array channel is averaged into one signal before MFCC extraction --
classification doesn't need the array's spatial information, only its
combined spectral content, unlike beamforming's own bearing estimate.
Same joblib/pickle security note as above applies here too.

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

- **API versioning**: header-based, not a `/v1/` path prefix -- routes
  stay at `/api/...` regardless of version. Send `X-API-Version: 1` to
  pin a request to the version your integration was written against; an
  unrecognized value gets 400 rather than being silently served whatever
  the current version happens to be. Every `/api/...` response carries
  `X-API-Version` naming the version actually served, whether or not the
  request asked for one. Only one version exists today (`1`) -- this is
  the mechanism a future breaking change would use, not evidence one has
  happened yet. See `app/api_version.py`.
- **Rate limiting**: two independent token buckets, both returning 429
  when exceeded. `POST /api/detections` is limited per `sensor_id`
  (`DRONE_RATE_LIMIT_PER_SECOND`/`_BURST`) -- a backstop against one
  malfunctioning or malicious sensor, not a normal-load limit. On top of
  that, a second bucket per *site* (`DRONE_GLOBAL_RATE_LIMIT_PER_SECOND`/
  `_BURST`, default 500/1000) catches the case the per-sensor limit
  can't: many distinct `sensor_id`s (real ones, or an attacker minting new
  ones specifically to dodge the per-sensor bucket -- nothing else stops
  an `ingest`-role key from claiming any `sensor_id`), each individually
  within its own limit, collectively overwhelming that site's share of the
  server. Scoped per-site rather than one deployment-wide bucket so one
  site's load can't starve another's, the same isolation guarantee every
  other resource in this app has (see "Multi-site" below).

  Bucket state is in-process (and resets on restart) on SQLite, but is
  transparently shared through a `rate_limit_bucket` table on PostgreSQL
  -- the same fix `cluster_association_lock` applies to detection
  association, for the same reason: with in-process buckets, two app
  replicas behind a load balancer would each independently allow up to
  the configured rate, so the *effective* limit becomes `replicas x
  configured limit` instead of the configured limit. Nothing to
  configure -- `app.ratelimit.RateLimiter` picks the shared path
  automatically whenever `DRONE_DATABASE_URL` points at PostgreSQL.
- **Clock-skew sanity check**: a detection whose (client-supplied)
  `timestamp` is more than `DRONE_MAX_DETECTION_CLOCK_SKEW_SECONDS`
  (default 300s) from the server's own clock, in either direction, is
  rejected with 400 -- not every cheap radar/RF sensor in a multi-sensor
  deployment is NTP-synced, and an undetected skew doesn't just misdraw a
  timestamp: `app.tracking`'s Kalman predict step uses the gap between a
  detection's timestamp and its track's last update as `dt`, so a sensor
  whose clock has drifted far ahead would inflate that gap hugely,
  ballooning the predicted position's uncertainty on every detection from
  that sensor. This endpoint assumes near-real-time ingestion, not
  historical backfill/replay of old recordings -- for that, load directly
  into the database or use a separate archival path, not `POST
  /api/detections`.
- **Retention**: the same periodic background sweep
  (`DRONE_RETENTION_SWEEP_INTERVAL_SECONDS`, default every hour) can purge
  three independent things, each off by default (0 days -- keeps
  everything forever, the previous behavior) so you opt into only what you
  need:
  - `DRONE_DETECTION_RETENTION_DAYS` -- raw detections older than this many
    days.
  - `DRONE_TRACK_RETENTION_DAYS` -- finished (closed/lost) tracks whose
    `last_seen` is older than this many days. An active track is never
    purged regardless of age. Purging a track detaches (sets `track_id` to
    `NULL` on) any detections and incidents that still reference it rather
    than deleting them -- detection retention above is a separate knob, and
    an incident's own record of what happened shouldn't disappear just
    because the track it pointed at aged out.
  - `DRONE_AUDIT_LOG_RETENTION_DAYS` -- audit log entries older than this
    many days. Kept independent because audit-trail compliance windows
    commonly outlive both raw sensor data and track history.
- **Track history/replay**: `GET /api/tracks/{track_id}/history` returns
  every detection that fed a track, oldest first -- a full replay of where
  it actually was over time, for post-incident review. Works the same for
  a closed/lost track as an active one: detection rows aren't deleted when
  a track closes, only purged by age via `DRONE_DETECTION_RETENTION_DAYS`
  (and the track row itself only by `DRONE_TRACK_RETENTION_DAYS`) above,
  so there's no separate "archive" to look in -- if retention hasn't
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
  for a spreadsheet. The track details panel's Export button drives this
  directly (format picker, downloads as `track-<uid>.<format>`) -- an
  operator doesn't need to know the endpoint exists. A Copy button next
  to it copies a plain-text summary of the selected track (classification,
  category, position, altitude, heading, speed, last seen) to the
  clipboard, for pasting into a chat/radio-log/incident note -- only
  fields the track actually has data for, "unknown" for the rest, never a
  guess.
- **Structured logging**: `DRONE_LOG_FORMAT=json` emits one JSON object per
  log line instead of human-readable text, for log aggregators.
- **Metrics**: `GET /api/metrics` in Prometheus exposition format -- two
  layers. Domain-specific: detections ingested (by sensor type), incidents
  opened (by type/severity), rate-limit rejections, clock-skew rejections,
  and live gauges for active tracks / open incidents. Generic HTTP-layer
  (`_RequestMetricsMiddleware`, `app/main.py`): `drone_http_requests_total`
  (by method/route template/status) and `drone_http_request_duration_seconds`
  (a latency histogram, same labels minus status) for every request, plus
  `drone_http_exceptions_total` specifically for requests that raised an
  unhandled exception rather than returning a normal (even error) response
  -- the signal to alert on for "something is actually broken", as opposed
  to a route's ordinary 4xx traffic. Labeled by route *template*
  (`/tracks/{track_id}`, not `/tracks/8412`) so this can't be turned into
  an unbounded-cardinality metrics blowup by however many distinct ids get
  requested, or by whatever a port scanner probes on a 404.
- **Outbound alerting**: set `DRONE_WEBHOOK_URLS` (comma-separated) to POST
  each incident's JSON to one or more generic webhooks when it opens.
  On top of that, `app/alerting.py` adds severity-routed integrations for
  Slack, PagerDuty, SMS (via Twilio), and Meshtastic, each independently
  configured (empty/unset = disabled) with its own minimum-severity
  threshold -- an escalation policy, so e.g. every incident can reach
  Slack for situational awareness while only `high`+ pages PagerDuty and
  only `critical` sends an SMS, instead of one severity treatment for
  every channel:

  | Channel | Enable with | Threshold var (default) |
  |---|---|---|
  | Slack | `DRONE_SLACK_WEBHOOK_URL` | `DRONE_SLACK_MIN_SEVERITY` (`low`) |
  | PagerDuty | `DRONE_PAGERDUTY_ROUTING_KEY` | `DRONE_PAGERDUTY_MIN_SEVERITY` (`high`) |
  | SMS (Twilio) | `DRONE_TWILIO_ACCOUNT_SID`/`_AUTH_TOKEN`/`_FROM_NUMBER` + `DRONE_SMS_TO_NUMBERS` | `DRONE_SMS_MIN_SEVERITY` (`critical`) |
  | Meshtastic | `DRONE_MESHTASTIC_HOSTNAME` | `DRONE_MESHTASTIC_MIN_SEVERITY` (`medium`) |

  Every channel is best-effort with a short timeout (`DRONE_ALERT_TIMEOUT_SECONDS`)
  -- a dead or misconfigured integration logs a warning and is skipped, it
  can't block incident handling or take the other channels down with it.

  **Meshtastic** is the odd one out on purpose: every other channel above
  needs internet or cell connectivity, which a genuinely off-grid
  deployment doesn't have. It talks to a Meshtastic node's TCP API
  (`meshtastic.tcp_interface.TCPInterface`) -- a node reachable on the
  local network (bridged onto the LAN over WiFi, or a Pi-attached radio),
  not the node's own LoRa radio directly:
  ```bash
  pip install -r requirements-meshtastic.txt
  export DRONE_MESHTASTIC_HOSTNAME=192.168.1.50   # the node's LAN address
  export DRONE_MESHTASTIC_CHANNEL_INDEX=0          # which mesh channel to send on
  ```
- **Mitigation-system notification** (`app/mitigation.py`, optional, off by
  default): a deliberate decision, not an omission -- this app is a
  passive-detection/tracking tool and doesn't own, drive, or claim any
  authority over mitigation hardware (an RF jammer, net gun, interdiction
  platform, ...), but it can *tell* one a qualifying incident opened.
  Structurally identical to the human-facing channels above (severity-
  gated, best-effort, short-timeout webhook POST), just aimed at an
  automated system instead of a person, and carrying what such a system
  actually needs to act -- the offending track's current position,
  classification, and heading/speed, not just "something happened."
  Enable with `DRONE_MITIGATION_WEBHOOK_URL`; `DRONE_MITIGATION_MIN_SEVERITY`
  (default `high`) gates it the same way the channels above do. What a
  receiving system does with the notification -- jam, track-and-follow,
  ignore -- is entirely its own decision and its own legal/operational
  responsibility, not this app's.
- **Message-queue fan-out** (`app/queue_publisher.py`, optional): set
  `DRONE_NATS_URL` (e.g. `nats://broker-host:4222`) to additionally publish
  a JSON copy of every ingested detection and opened incident onto a NATS
  core subject (`DRONE_NATS_DETECTION_SUBJECT`/`_INCIDENT_SUBJECT`,
  defaulting to `drone.detections`/`drone.incidents`) alongside the normal
  in-process handling -- unset (the default) disables it entirely, with no
  behavior change. A consumer process (or several, elsewhere) can subscribe
  to these subjects for fan-in aggregation, cross-site correlation, or a
  separate analytics pipeline, without touching the ingest API or the
  tracker. It does **not** make ingest/association/fusion itself
  queue-based -- `POST /api/detections` stays exactly the synchronous
  single-process design described above, unaffected either way. Implemented
  as a minimal NATS core PUB/SUB client over a raw socket rather than a
  full client library, so it adds no new dependency.
- **Queue-based ingest** (`app/consumer.py`, optional, opt-in): a second,
  additional front door into the same tracking pipeline `POST
  /api/detections` uses -- for a deployment that wants ingest processing
  decoupled from (and independently scalable from) the API process, run
  `python -m app.consumer` and publish raw detection JSON onto
  `DRONE_NATS_RAW_DETECTION_SUBJECT` (default `drone.detections.raw`,
  deliberately a different subject from the fan-out one above, which
  carries *already-processed* detections) instead of calling the HTTP
  endpoint. `DRONE_CONSUMER_QUEUE_GROUP` (default `drone-consumers`) lets
  several consumer processes share one NATS queue group so each raw
  detection is load-balanced to exactly one of them, the actual mechanism
  a horizontally-scaled worker pool needs -- not a copy fanned out to
  every consumer. `DRONE_CONSUMER_SITE_NAME` scopes everything this
  consumer processes to one site (the default site if unset); there's no
  per-message credential to derive a site from the way an API key's
  `"site"` field does for the HTTP path, so run a separate consumer
  (against a separate subject, if needed) per site. This is purely
  additive -- `POST /api/detections`'s behavior, including its response
  contract, never changes regardless of whether any consumer is running.

## Load testing

`scripts/load_test.py` measures real throughput/latency against a running
instance -- not a pass/fail CI gate (the numbers are a property of the
machine running it, not a fixed correctness threshold), just a documented,
reproducible way to generate them:

```bash
python scripts/load_test.py --url http://127.0.0.1:8000 --mode single --sensors 20 --duration 15
python scripts/load_test.py --url http://127.0.0.1:8000 --mode batch --batch-size 30 --requests 100
```

**CI's `load-smoke` job** runs this script too, but for a different reason
than the benchmarking below: not to measure throughput (meaningless as a
CI gate for the reason above), but to catch a bug that only shows up under
concurrent load -- a deadlock, an unhandled exception, a race in detection
association -- that the single-request-at-a-time pytest suite can't
exercise. `--fail-on-errors` gates on *hard* errors (5xx responses or a
request that never completed) only, not on an ordinary 429 -- the rate
limiter correctly rejecting excess load under this script's intentionally
saturating traffic is expected, not a regression, and would otherwise make
this job flaky for a reason that has nothing to do with correctness.

## Recording and replaying detection traffic

`scripts/replay_detections.py` records real detection traffic from a
running instance's `GET /api/detections` and replays it against another
(or the same, later) instance -- useful for reproducing a tracking/
incident bug against a fresh DB without waiting for it to recur live, or
for feeding a demo/staging deployment realistic-looking traffic without
sensors attached:

```bash
# Save an hour of traffic from a source server to a file:
python scripts/replay_detections.py record --url http://source:8000 \
    --start 2026-01-01T00:00:00 --end 2026-01-01T01:00:00 --out captured.jsonl

# Replay it against a target server at 4x speed, timestamps rewritten to
# "now" (a stale timestamp would just be rejected -- see
# DRONE_MAX_DETECTION_CLOCK_SKEW_SECONDS above) with the recorded
# inter-detection spacing preserved so a track's motion looks like it did
# the first time instead of arriving all at once:
python scripts/replay_detections.py replay --url http://target:8000 \
    --in captured.jsonl --speed 4 --api-key $DRONE_API_KEY
```

Like `load_test.py`, this isn't part of the pytest suite or CI -- it talks
to a real running server, not a fixture.

## Terminal admin client

`scripts/drone_cli.py` is a terminal client for a running instance's HTTP
API -- for the field-kit scenario where an operator needs to check tracks/
incidents/sensors or acknowledge/resolve an incident but the dashboard's
browser (or its Leaflet/CDN map tiles) isn't available, and for scripting
routine checks without hand-rolling curl/jq. Every subcommand wraps one
existing API endpoint; it adds no server-side behavior of its own:

```bash
python scripts/drone_cli.py --url http://127.0.0.1:8000 tracks list --status active
python scripts/drone_cli.py --url http://127.0.0.1:8000 incidents list --status open
python scripts/drone_cli.py --url http://127.0.0.1:8000 incidents acknowledge 42
python scripts/drone_cli.py --url http://127.0.0.1:8000 incidents resolve 42
python scripts/drone_cli.py --url http://127.0.0.1:8000 sensors list
python scripts/drone_cli.py --url http://127.0.0.1:8000 zones list
```

`--url`/`--api-key` can also come from `DRONE_URL`/`DRONE_API_KEY` env
vars. Add `--json` to any command for raw JSON instead of a table (for
piping into `jq` or another script).

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

## Versioning

`app.__version__` (`app/__init__.py`) is the single source of truth for
the running version -- surfaced in `GET /api/health`'s `version` field and
the OpenAPI schema (`/docs`, `/openapi.json`), so "what's actually
deployed" is answerable by asking the running process rather than
comparing it against git history. `CHANGELOG.md` follows [Keep a
Changelog](https://keepachangelog.com/en/1.1.0/) and [Semantic
Versioning](https://semver.org/); when a change is worth calling out to
someone upgrading a running deployment, add it under that file's
`[Unreleased]` heading. Cutting a release means bumping `__version__`,
retitling `[Unreleased]` with the version and date, and starting a fresh
`[Unreleased]` section above it -- no separate release-automation tooling,
by design: this is a self-hosted service deployed from a git checkout or
a Docker image tag, not a package published anywhere that would need one.

## Configuration

All settings are environment variables with working defaults — nothing
needs to be set to run locally.

| Variable | Default | Purpose |
|---|---|---|
| `DRONE_HOST` | `127.0.0.1` | Bind host |
| `DRONE_PORT` | `8000` | Bind port |
| `DRONE_DB_PATH` | `data/drone_sensor.db` | SQLite file location (used to build the default `DRONE_DATABASE_URL`) |
| `DRONE_DATABASE_URL` | `sqlite:///<DRONE_DB_PATH>` | SQLAlchemy database URL; point at PostgreSQL for production |
| `DRONE_DB_POOL_SIZE` | `5` | PostgreSQL connection pool size (ignored for SQLite) |
| `DRONE_DB_MAX_OVERFLOW` | `10` | PostgreSQL pool overflow above `DRONE_DB_POOL_SIZE` before a connection request waits (ignored for SQLite) |
| `DRONE_ZONES_SEED_PATH` | `app/zones.seed.json` | Zone seed file, loaded at startup |
| `DRONE_RF_SIGNATURES_PATH` | unset | Operator-supplied RF signatures JSON file (see "RF signature fingerprinting" below); no default -- unlike zones, this app ships no bundled file since it has no real per-model data to bundle |
| `DRONE_ML_MODEL_PATH` | unset | Trained model file (see "ML-based classification" above); no default -- this app ships no trained model |
| `DRONE_ML_CONFIDENCE_THRESHOLD` | `0.6` | Minimum top-class probability before a configured model's prediction is trusted over the rule-based classifier |
| `DRONE_ACOUSTIC_ML_MODEL_PATH` | unset | Trained acoustic model file (see "Acoustic classification" above); no default -- this app ships no trained model |
| `DRONE_ACOUSTIC_ML_CONFIDENCE_THRESHOLD` | `0.6` | Same role as `DRONE_ML_CONFIDENCE_THRESHOLD`, for the acoustic model |
| `DRONE_LOG_LEVEL` | `INFO` | Logging level |
| `DRONE_LOG_FORMAT` | `text` | `text` or `json` (structured, one object per line) |
| `DRONE_API_KEY` | *(unset)* | Legacy single key, granted the `admin` role. Prefer `DRONE_API_KEYS` for real deployments |
| `DRONE_API_KEYS` | *(unset)* | JSON object mapping each key to a role (`ingest`\|`viewer`\|`operator`\|`admin`, as a bare string) or `{"role": ..., "site": "site-name", "label": ..., "expires_at": ..., "revoked": ...}` to also scope/label/expire/revoke that key -- see "Multi-site" and "Key lifecycle" below |
| `DRONE_CORS_ORIGINS` | *(unset)* | Comma-separated origins allowed to make cross-origin browser requests; unset means no CORS headers at all (default, same as before this existed). Only needed for a frontend hosted on a different origin than this API |
| `DRONE_RATE_LIMIT_PER_SECOND` | `50` | Per-sensor detection ingest rate limit |
| `DRONE_RATE_LIMIT_BURST` | `100` | Per-sensor token-bucket burst capacity |
| `DRONE_GLOBAL_RATE_LIMIT_PER_SECOND` | `500` | Site-wide detection ingest rate limit, on top of the per-sensor one |
| `DRONE_GLOBAL_RATE_LIMIT_BURST` | `1000` | Site-wide token-bucket burst capacity |
| `DRONE_MAX_BATCH_SIZE` | `500` | Max detections per `POST /api/detections/batch` request |
| `DRONE_MAX_DETECTION_CLOCK_SKEW_SECONDS` | `300` | Reject a detection whose timestamp is further than this from the server's clock |
| `DRONE_DETECTION_RETENTION_DAYS` | `0` (disabled) | Purge detections older than this many days |
| `DRONE_TRACK_RETENTION_DAYS` | `0` (disabled) | Purge finished (closed/lost) tracks whose `last_seen` is older than this many days; active tracks are never purged |
| `DRONE_AUDIT_LOG_RETENTION_DAYS` | `0` (disabled) | Purge audit log entries older than this many days |
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
| `DRONE_MESHTASTIC_HOSTNAME` | *(unset)* | LAN address of a Meshtastic node's TCP API, for off-grid alerting |
| `DRONE_MESHTASTIC_PORT` | `4403` | Meshtastic node's TCP API port |
| `DRONE_MESHTASTIC_CHANNEL_INDEX` | `0` | Which mesh channel to send incident alerts on |
| `DRONE_MESHTASTIC_MIN_SEVERITY` | `medium` | Minimum incident severity that sends a Meshtastic alert |
| `DRONE_ALERT_TIMEOUT_SECONDS` | `5` | Per-request timeout for Slack/PagerDuty/SMS alerts |
| `DRONE_NATS_URL` | *(unset)* | NATS broker URL to additionally publish detections/incidents to; unset disables it |
| `DRONE_NATS_DETECTION_SUBJECT` | `drone.detections` | NATS subject each ingested detection is published to |
| `DRONE_NATS_INCIDENT_SUBJECT` | `drone.incidents` | NATS subject each opened incident is published to |
| `DRONE_NATS_CONNECT_TIMEOUT_SECONDS` | `2` | Connect/send timeout for the NATS publisher |
| `DRONE_NATS_RAW_DETECTION_SUBJECT` | `drone.detections.raw` | NATS subject `app/consumer.py` subscribes to for queue-based ingest |
| `DRONE_CONSUMER_QUEUE_GROUP` | `drone-consumers` | NATS queue group -- shared by multiple consumer processes to load-balance ingest across them |
| `DRONE_CONSUMER_SITE_NAME` | *(unset -- default site)* | Which site `app/consumer.py` scopes every detection it processes to |
| `DRONE_OIDC_ISSUER_URL` / `_CLIENT_ID` / `_CLIENT_SECRET` | *(unset)* | Generic OIDC SSO login (see "SSO login" above) -- all three required to enable it |
| `DRONE_OIDC_REDIRECT_URL` | *(unset -- computed from the request)* | Explicit OIDC redirect URI, for a deployment behind a proxy that rewrites the host |
| `DRONE_OIDC_ROLE_CLAIM` / `_SITE_CLAIM` | `role` / `site` | Which ID token claims map to this app's role/site model |
| `DRONE_OIDC_DEFAULT_ROLE` | `viewer` | Role granted when the role claim is missing/unrecognized |
| `DRONE_OIDC_SESSION_SECRET` | *(unset -- required to enable SSO)* | Fernet key sealing the SSO session cookie |
| `DRONE_OIDC_SESSION_MAX_AGE_SECONDS` | `28800` (8h) | How long an SSO session cookie stays valid |
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
| `DRONE_INCIDENT_CORROBORATION_MIN_SENSOR_TYPES` | `2` | Distinct sensor types needed to escalate an incident's severity one level |
| `DRONE_CLASSIFICATION_CONFIDENCE_DECAY_SECONDS` | `300` | Time for a stale track's `classification_confidence` to decay to the floor |
| `DRONE_CLASSIFICATION_CONFIDENCE_FLOOR` | `0.3` | Floor `classification_confidence` decays toward, never below |
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

**SSO login (generic OpenID Connect)**: an alternative to `DRONE_API_KEY(S)`
for a human operator logging into the dashboard through a browser — not a
replacement for it (a sensor's own ingest key still authenticates the same
way). Works with any standards-compliant OIDC provider (Okta, Auth0, Azure
AD, Google Workspace, a self-hosted Keycloak, ...) via issuer discovery, no
vendor-specific code:

```bash
pip install -r requirements-oidc.txt   # authlib -- not needed unless you set these
export DRONE_OIDC_ISSUER_URL="https://your-idp.example.com"
export DRONE_OIDC_CLIENT_ID="..."
export DRONE_OIDC_CLIENT_SECRET="..."
export DRONE_OIDC_SESSION_SECRET="$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")"
```

That's the minimum to turn it on — visiting `/auth/login` redirects to your
IdP, and a successful login sets a session cookie good for
`DRONE_OIDC_SESSION_MAX_AGE_SECONDS` (default 8h; `/auth/logout` clears it
early). Every route under `/auth/` 404s until all three of
`ISSUER_URL`/`CLIENT_ID`/`CLIENT_SECRET` are set, so a deployment that
never opts in gets no new attack surface. `DRONE_OIDC_SESSION_SECRET` has
no default — unlike every other secret in this app, a blank/predictable
session-signing key would let anyone forge an admin session, so the app
fails closed (won't create or verify a session) rather than falling back to
something guessable.

An ID token's claims map to this app's own role/site model via two
configurable claim names (every IdP names things differently, so this
doesn't assume one): `DRONE_OIDC_ROLE_CLAIM` (default `"role"` — must
resolve to `ingest`/`viewer`/`operator`/`admin` or it falls back to
`DRONE_OIDC_DEFAULT_ROLE`, default `viewer`) and `DRONE_OIDC_SITE_CLAIM`
(default `"site"`, a site *name* — same as `DRONE_API_KEYS`' own `"site"`
field — or the default site if absent/unset). An `X-API-Key` header still
takes priority over a session cookie when both are present, so existing
sensor/automation integrations are entirely unaffected by turning this on.

The dashboard surfaces this itself, not just the raw `/auth/login`
redirect: `GET /auth/config` (unauthenticated) tells it whether SSO is
even configured, so the auth bar's "or log in with SSO" link only appears
on a deployment that actually turned it on. Once authenticated (either
way), `GET /api/me` reports who as — the topbar shows `name (role)`, with
a "Log out" link (`/auth/logout`) that only appears for an SSO session,
since a static API key has nothing to log out of.

**Multi-site**: every record (tracks, detections, incidents, zones,
sensor/operator registrations) belongs to exactly one *site* (a physical
site/campus this deployment monitors — `app/sites.py`, `app/db.py`), and
every API key is scoped to exactly one site. A fresh or just-upgraded
deployment has exactly one, auto-created site named `default`, so nothing
about single-site usage changes unless you deliberately set up more than
one. To do that:

1. Create the additional site(s) (admin-only):
   ```bash
   curl -X POST http://127.0.0.1:8000/api/sites \
     -H "X-API-Key: $ADMIN_KEY" -H "Content-Type: application/json" \
     -d '{"name":"warehouse-north"}'
   ```
2. Scope a key to it via `DRONE_API_KEYS`' per-key `"site"` field (the
   pre-multi-site bare-role-string format, `{"key":"role"}`, still works
   unchanged and resolves to the `default` site):
   ```bash
   export DRONE_API_KEYS='{
     "north-radar-key": {"role": "ingest", "site": "warehouse-north"},
     "north-ops-key": {"role": "operator", "site": "warehouse-north"},
     "south-radar-key": {"role": "ingest"}
   }'
   ```

A key only ever sees and acts on its own site's data — there is no
cross-site key in this version, and a track/incident/zone id from another
site 404s rather than leaking a 403 that would confirm it exists. Any
`admin`-role key can list/create sites (`GET`/`POST /api/sites`)
regardless of which site it's scoped to — there's no separate
"deployment owner" vs. "site admin" distinction yet, so treat every admin
key as trusted with the whole deployment's site catalog, not just its own
site's data.

**Known limitation**: `sensor_id` (sensor registrations) and
`operator_id` (authorized operators) must still be globally unique across
every site in one deployment, not just within a site — making that
composite-keyed would need rebuilding those tables on upgrade, a bigger
migration than this version takes on. Namespace them (e.g.
`warehouse-north-radar-1`) if two sites would otherwise pick the same
sensor/operator id independently.

**Key lifecycle**: a `DRONE_API_KEYS` entry can carry `"label"`,
`"expires_at"`, and `"revoked"` alongside `"role"`/`"site"`:

```bash
export DRONE_API_KEYS='{
  "north-radar-key": {"role": "ingest", "site": "warehouse-north", "label": "north-radar-1"},
  "contractor-key": {"role": "operator", "expires_at": "2027-03-01T00:00:00", "label": "acme-contractor"}
}'
```

`expires_at` (ISO-8601, evaluated in UTC) makes a key stop working past
that timestamp without needing a redeploy to pull it; `revoked: true` does
the same immediately. Both fail closed with a 401, the same as an unknown
key — a revoked/expired key never gets far enough to leak which role it
used to have via a 403. `label` is what shows up as the actor in the
audit log (below) and in `GET /api/admin/keys` (admin-only) instead of a
bare role name — useful once more than one key shares a role. There's no
separate revocation list or database table for keys themselves: since
keys already live in `DRONE_API_KEYS`, revoking one is just editing that
JSON (or, for `expires_at`, doing nothing and letting the clock do it) —
**and restarting the process**, since the plain `DRONE_API_KEYS` env var
is only read once at startup. For a revocation that takes effect
immediately with no restart, use `DRONE_API_KEYS_FILE` instead (below).

**Secrets from files, and key rotation without a restart**: `DRONE_API_KEY`/
`DRONE_API_KEYS` (and the alerting credentials below —
`DRONE_SLACK_WEBHOOK_URL`, `DRONE_PAGERDUTY_ROUTING_KEY`,
`DRONE_TWILIO_ACCOUNT_SID`/`_AUTH_TOKEN`, `DRONE_MITIGATION_WEBHOOK_URL`,
`DRONE_FAA_NOTAM_CLIENT_SECRET`) each also accept a `_FILE`-suffixed
variant (e.g. `DRONE_API_KEYS_FILE=/run/secrets/drone_api_keys.json`) that
reads the secret from that file's content instead — the same convention
Docker/Kubernetes secrets use (e.g. `POSTGRES_PASSWORD_FILE` in the
official `postgres` image). Safer than a raw env var, which is visible to
anything that can read `/proc/<pid>/environ` or run `docker inspect` on
the container; a file mounted from a real secret store can be
permissioned/audited independently of the process's own environment.

For `DRONE_API_KEYS_FILE` specifically, this also means a key can be
**rotated with no restart**: `app.auth.configured_keys()` already
re-reads its source on every request (that's what makes editing
`DRONE_API_KEYS` in a test take effect immediately, no reload step), so
updating the file the app is watching takes effect on the very next
request. `scripts/rotate_api_key.py` generates a new key and can add/remove
one from a `DRONE_API_KEYS_FILE`-style JSON file in place:

```bash
# Add a new key to the file, keeping the old one active too (overlap window):
python scripts/rotate_api_key.py add /run/secrets/drone_api_keys.json --role ingest --label radar-1
# ... update whatever used the old key to the new one ...
# Once nothing authenticates with the old key any more (check
# GET /api/admin/keys' last_used_at for it), remove it:
python scripts/rotate_api_key.py remove /run/secrets/drone_api_keys.json --key <old-key>
```

`GET /api/admin/keys` also reports each key's last-used time and use
count — useful for spotting a key nobody's used in months (a candidate to
revoke) without grepping logs. That usage tracking is deliberately
throttled to about once per 30 seconds per key (`app/auth.py`'s
`_KEY_USAGE_FLUSH_INTERVAL_S`), not updated on literally every request:
a real database write on every single authenticated call — including
every detection a sensor posts — would add write load to exactly the
path this app is most performance-sensitive about, for a feature where
"last used within the last 30 seconds" carries the same practical
information as "last used at this exact millisecond."

**Audit log**: `GET /api/audit-log` (admin-only) records who did what for
the admin actions that can change what the system trusts or silence a
real alert — registering a sensor or authorized operator, creating a
site, and acknowledging/resolving an incident — each entry naming the
acting key's label (or role, if unlabeled; never the raw key), what it
did, and what it acted on. It's not a general activity log for
everything the app does — detections/tracks/incidents already carry
their own timestamps for that — specifically the actions that otherwise
have no other record of *who* performed them.

**Dependency vulnerability scanning**: a `security` CI job runs
[`pip-audit`](https://github.com/pypa/pip-audit) against every
`requirements*.txt` on each push/PR, checking pinned versions against the
Python Packaging Advisory Database. This is how `cryptography` (used for
the Ed25519 Remote ID signature scheme -- see "Classification fusion &
friendly allowlist" above) was found pinned to `41.0.7`, a version with
several known CVEs, and bumped to `50.0.0`; none of the fixed CVEs were in
the Ed25519 raw-key code path this app actually uses (they were X.509
chain validation, PKCS#7, and bundled-OpenSSL issues), but pinning to a
version with zero known vulnerabilities is the point, not just "was the
specific bug exploitable here." Run it locally the same way CI does:

```bash
pip install pip-audit
pip-audit -r requirements.txt -r requirements-postgres.txt -r requirements-dev.txt
```

**Static security analysis**: the same `security` CI job also runs
[`bandit`](https://github.com/PyCQA/bandit) against `app/`, checking this
app's own code for security-relevant patterns (unsafe deserialization,
hardcoded secrets, weak crypto, unescaped XML output, etc.) that pip-audit
can't see, since pip-audit only checks third-party dependency CVEs. Every
finding bandit currently raises against this codebase was reviewed and is
individually justified (not blanket-suppressed) in `pyproject.toml`'s
`[tool.bandit]` -- e.g. flagged `urllib.urlopen` calls all use
operator-configured or hardcoded HTTPS URLs, never attacker-controlled
input; the flagged `xml.etree`/`xml.sax` usage only builds/escapes outgoing
XML, never parses untrusted input. Run it locally the same way CI does:

```bash
pip install bandit
bandit -r app/ -c pyproject.toml
```

## Project layout

```
app/
  main.py               FastAPI app, routes, RBAC wiring, retention sweep, dashboard
  models.py             Pydantic domain models
  db.py                 SQLAlchemy Core storage helpers (SQLite + PostgreSQL)
  schema.py             SQLAlchemy table definitions (the schema, portable across backends)
  config.py             Environment-variable settings
  logging_config.py     Logging setup (text or JSON)
  auth.py               API key + role-based access control (RBAC) + per-key site scoping
  api_version.py         Header-based API versioning (X-API-Version)
  oidc.py                 Generic OpenID Connect login (optional -- requirements-oidc.txt)
  sso_session.py          The session cookie app/oidc.py issues (verifiable with zero OIDC deps)
  sites.py               Default-site bootstrap for the multi-site migration (see app/db.py)
  ratelimit.py          Per-sensor token-bucket rate limiter
  metrics.py             Prometheus counters
  notifications.py       Outbound webhook alerting on incident open
  classification.py     Single-detection sensor + confidence -> label rule
  ml/                     Optional ML classification scaffolding (no trained model shipped -- see below)
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
  static/dashboard.html   Dashboard (no build step; Leaflet is vendored locally -- see static/vendor/ --
                          only map tile imagery itself still comes over the network)
  static/vendor/leaflet/  Leaflet, vendored (not loaded from a CDN) so the dashboard has no third party
                          in its trust chain; `npm pack leaflet@<version>` to update
tests/                     Pytest suite (runs against SQLite by default, PostgreSQL optionally)
tests_e2e/                 Browser-driven dashboard tests (Playwright, separate CI job -- see its README.md)
simulator.py               Posts realistic detections against a running server
main.py                    Entrypoint (python main.py)
Dockerfile                 Single-stage container build
```
