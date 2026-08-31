# Changelog

All notable changes to this project are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning follows [Semantic Versioning](https://semver.org/) once past
1.0.0 -- pre-1.0 (`0.x.y`), a minor bump (`0.x`) may still include a
breaking change, same as semver's own pre-1.0 convention. `app/__version__`
(see `app/__init__.py`) is the single source of truth for the running
version; it's surfaced in `GET /api/health` and the OpenAPI schema so
"what's actually deployed" is answerable by asking the running process.

When you make a change worth calling out to someone upgrading a running
deployment, add a bullet under `## [Unreleased]` in the relevant category
(Added / Changed / Fixed / Security). When you cut a release, retitle that
section with the version and date, bump `__version__`, and start a fresh
`## [Unreleased]` above it.

## [Unreleased]

### Added

- `POST /api/incidents/{id}/resolve` Resolve button in the dashboard's Alerts panel (the endpoint already existed; there was no way to reach it from the UI).
- Incidents auto-close when their trigger condition clears (track exits the zone, leaves formation, stops loitering) instead of staying open until a human manually resolves them.
- `GET /api/detections` for raw, track-independent detection queries (`sensor_id`/`track_id`/`start`/`end`/`limit`/`offset`), for sensor-level QA without first knowing which track a detection belongs to.
- `MISSING` sensor status: `GET /api/sensors` now cross-references registered sensor positions, so a sensor that's registered but has gone silent is distinguishable from one that was never registered at all.
- Cross-sensor incident severity escalation (an incident corroborated by multiple independent sensor types escalates in severity) and classification confidence decay (a track's classification confidence decays toward a floor the longer it goes without a fresh detection, applied at read time).
- FAA NOTAMs can now be auto-converted into zones (`import_notams_as_zones` in `app/airspace/faa_notam.py`), using the exact queried search circle as an honest geometric approximation rather than guessing at a NOTAM's real (often absent) shape.
- Zone polygon validator/builder CLI (`python -m app.adapters.validate_zone`): checks a hand-authored/drawn polygon for structural validity (self-intersection, out-of-range coordinates, too few vertices) before it goes into `zones.seed.json` or `POST /api/zones`.
- Detection record/replay tool (`scripts/replay_detections.py`): captures real detection traffic from a running instance and replays it against another, with recorded inter-detection timing preserved (scaled by `--speed`) -- for reproducing a bug against a fresh DB or feeding a demo deployment realistic traffic without sensors attached.
- Terminal admin CLI (`scripts/drone_cli.py`): tracks/incidents/sensors/zones from the command line, for the field-kit scenario where the dashboard's browser isn't available.
- `app/adapters/sdk.py`: shared POST-to-`/api/detections` + argparse plumbing now used by every detection-posting adapter, replacing each one's previously-duplicated urllib POST function; adds `--max-retries`/`--retry-backoff` and a `format_post_error` helper (surfaces the server's actual JSON error detail, not just an HTTP status line) that no adapter had before.
- `GET /auth/config` and `GET /api/me`, plus dashboard UI (an "or log in with SSO" link and a "name (role)" / "Log out" indicator) surfacing the existing OIDC SSO login flow, which previously had no entry point in the dashboard itself.
- Track search (multi-field, multi-term, clear button, `/` shortcut), a track-list sort control and active-alert indicator, and track-details-panel improvements (aircraft category field, copy-to-clipboard, export).
- Aircraft-category-based map markers: a quadcopter glyph for drone/unknown tracks, and a realistic airplane silhouette with altitude-based coloring for classified aircraft.

### Fixed

- Startup crash on a fresh checkout: the SQLite data directory wasn't created before the app tried to open its database file there.
- Zone name uniqueness was check-then-act (a TOCTOU race under concurrent creates); now enforced with a database-level unique index, translated to a 409 on conflict.
- `update_incident` silently dropped a changed `severity` field.
- SQLite now runs in WAL mode (was DELETE mode), and the dashboard surfaces non-auth API errors (e.g. a failed acknowledge) as a toast instead of only logging to the console.
- `acoustic_array_bridge.py` now validates `--mic-positions`/`--assumed-range-m` upfront and surfaces a POST failure's actual error body, instead of failing deep inside a beamforming call or printing an unhelpful HTTP status line.

## [0.1.0] - 2026-08-22

First version-tracked baseline. This project didn't tag releases or keep
a changelog before this point -- rather than fabricate a history the git
log doesn't actually record cleanly release-by-release, this entry marks
the point version tracking started, on top of an already-substantial,
tested system: multi-sensor detection ingest and Kalman/IMM tracking,
zone-incursion and behavioral (loitering/formation/shadowing) incident
detection, a live dashboard, multi-site isolation, CoT/NATS/webhook
output integrations, and the operational tooling in this same change
(data retention, automated backups, Prometheus metrics, Dependabot,
graceful shutdown, a load-smoke CI job). See the git log and README.md
for the detail a changelog entry can't practically summarize retroactively.
