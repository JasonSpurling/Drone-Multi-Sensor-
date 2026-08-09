# Drone Multi-Sensor

Ingests detections from multiple sensor types, fuses them into tracks,
classifies each track, flags restricted-zone incursions as incidents, and
shows it all on a live dashboard.

## Setup

```
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Run

```
.venv\Scripts\python.exe main.py
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

```
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
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

```
.venv\Scripts\python.exe simulator.py
.venv\Scripts\python.exe simulator.py --ticks 50 --interval 0.5 --seed 42
.venv\Scripts\python.exe simulator.py --api-key <your key>   # if DRONE_API_KEY is set on the server
```

Watch the dashboard while it runs to see tracks appear, get classified, and
trigger a zone-incursion alert.

## API

| Endpoint | Description |
|---|---|
| `GET /api/health` | Liveness check |
| `POST /api/detections` | Ingest a detection; runs track association, classification, and zone-incident checks |
| `GET /api/tracks` / `GET /api/tracks/{id}` | List or fetch tracks (`?status=active\|lost\|closed`) |
| `GET /api/incidents` | List incidents (`?status=open\|acknowledged\|resolved`) |
| `POST /api/incidents/{id}/acknowledge` | Acknowledge an open incident |
| `GET /api/zones` | List active zones |
| `GET /api/sensors` | Per-sensor health, derived from each sensor's most recent detection |

## Configuration

All settings are environment variables with working defaults — nothing
needs to be set to run locally.

| Variable | Default | Purpose |
|---|---|---|
| `DRONE_HOST` | `127.0.0.1` | Bind host |
| `DRONE_PORT` | `8000` | Bind port |
| `DRONE_DB_PATH` | `data/drone_sensor.db` | SQLite file location |
| `DRONE_ZONES_SEED_PATH` | `app/zones.seed.json` | Zone seed file, loaded at startup |
| `DRONE_LOG_LEVEL` | `INFO` | Logging level |
| `DRONE_API_KEY` | *(unset)* | If set, all endpoints except `/api/health` require a matching `X-API-Key` header |
| `DRONE_TRACK_TIME_GATE_SECONDS` | `30` | Max age gap for a detection to join a track |
| `DRONE_TRACK_DISTANCE_GATE_M` | `500` | Max distance for a detection to join a track |
| `DRONE_TRACK_STALE_SECONDS` | `30` | Active track goes `lost` after this many quiet seconds |
| `DRONE_TRACK_DROP_SECONDS` | `300` | Lost track goes `closed` after this many more |
| `DRONE_CONFIDENCE_THRESHOLD` | `0.75` | Confidence at/above which a detection is classified `drone` |
| `DRONE_BIRD_CONFIDENCE_THRESHOLD` | `0.4` | Below this, a camera/acoustic detection is classified `bird` |
| `DRONE_SENSOR_ONLINE_SECONDS` | `60` | Sensor shows `online` if seen within this window |
| `DRONE_SENSOR_STALE_SECONDS` | `300` | Sensor shows `stale` up to this window, `offline` beyond it |

## Security

By default the app binds to `127.0.0.1` and requires no authentication —
fine as-is, since nothing outside this machine can reach it. If you ever
want to reach it from another device (phone, another PC on your LAN),
**set `DRONE_API_KEY` before changing `DRONE_HOST`**:

```powershell
$env:DRONE_API_KEY = "some long random string"
.venv\Scripts\python.exe main.py
```

Once set, every `/api/*` request (except `/api/health`) needs a matching
`X-API-Key` header, or it gets a 401. The dashboard will prompt for the key
inline the first time it hits a 401, then remembers it in the browser's
`localStorage`. The simulator picks it up from `--api-key` or the
`DRONE_API_KEY` environment variable.

Without a key set, don't expose the port beyond localhost — anyone who can
reach it could inject fake detections or acknowledge (silence) real alerts.

## Project layout

```
app/
  main.py            FastAPI app, routes, dashboard
  models.py           Pydantic domain models
  db.py                SQLite storage helpers
  schema.sql          SQLite schema
  config.py           Environment-variable settings
  logging_config.py   Logging setup
  auth.py              API key check (active only if DRONE_API_KEY is set)
  classification.py   Sensor + confidence -> label rules
  tracking.py          Track association, update, expiry
  incidents.py         Zone-incursion incident creation
  zones.py            Zone loading + point-in-polygon test
  sensors.py           Sensor health
  zones.seed.json     Sample restricted zone
  api/                 Route handlers, one module per resource
  static/dashboard.html  Self-contained dashboard (no build step)
simulator.py            Posts realistic detections against a running server
main.py                  Entrypoint (python main.py)
```
