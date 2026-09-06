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

- Console-style dashboard redesign: panel switching moved from a floating icon rail into a persistent top nav bar; a radar-style polar view (concentric range rings + compass bearing) in track details, plotted from the nearest registered sensor using real geodesic math; a "Nearest zone" distance row.
- `POST /api/tracks/{id}/classify`: an operator's deliberate Friend/Foe classification override, distinct from `app.fusion`'s automated evidence-weighted vote -- surfaced as Friend/Foe buttons in the dashboard.
- `GET /api/sensors/{sensor_id}/live`: a real MJPEG live-view proxy for a registered camera's RTSP/HTTP stream (throttled to 8 FPS), surfaced in the dashboard's track details panel for the nearest camera sensor. Requires `requirements-camera.txt`'s opencv extra; returns `501` with that instruction otherwise, never a fake/empty stream. `SensorRegistration.camera_stream_url` is write-only (always reported as `null`) since it commonly embeds login credentials.
- A "Signal" section in track details surfacing a track's most recent detection's real per-sensor `raw_data` fields (RF center frequency/bandwidth, acoustic bearing confidence, ...) -- previously captured but not shown anywhere in the UI.
- Verified/Unverified track grouping: `Track.verified` (and `Track.corroborating_sensor_types`) is now computed at read time on every track-serving endpoint, true once 2+ distinct sensor types (`INCIDENT_CORROBORATION_MIN_SENSOR_TYPES`, the same threshold `app.incidents` already uses to escalate severity) have reported on it. The dashboard's Tracks panel splits into Verified/Unverified sub-tabs with collapsible classification groups underneath; a separate, independent Verified/Unverified filter at the bottom of the map controls which diamonds actually plot there.
- A tab strip (Live view / Quality / Signal / Radar / Image) in the dashboard's track details panel, replacing the previous always-stacked sections.
- `GET /api/sensors/{sensor_id}/snapshot`: one on-demand still JPEG frame from a registered camera's stream (not a stored-image history -- each request opens the stream fresh), surfaced as a "Take snapshot" button under the details panel's Image tab.
- Read-only PTZ slew-to-cue display under the Live view tab, showing the pan/tilt/distance the camera would need to point at the track right now (`GET /api/tracks/{id}/cue/{sensor_id}`, which already existed) -- deliberately never sends a command to the camera.
- A live-view inset thumbnail, shown only when a second, genuinely distinct camera sensor is registered near the same track -- never a duplicate of the main feed.
- A topbar ALERT banner, shown only while at least one incident is genuinely open/unacknowledged, with the count and worst severity; clicking it opens the Alerts panel.
- `POST /api/tracks/{id}/classify` now also accepts `unknown` ("Neutral" in the dashboard): clears a previous Friend/Foe override and lets automatic classification start fresh, rather than only ever being able to set friendly/drone.
- `POST /api/tracks/{id}/ignore`: an operator's "stop alerting on this" suppression (`Track.ignored`). A zone/behavioral incident is never opened for an ignored track; the track itself keeps updating and showing up everywhere else unchanged. Surfaced as an Ignore/Unignore button in the dashboard, which also dims an ignored track's card and adds an "Ignored" badge.
- `Track.contributing_sensor_types`: which distinct sensor types (not just how many) have corroborated a track, surfaced on the dashboard's map marker tooltip when a track is selected, alongside its Verified/Unverified state.
- A "Duration" (first_seen -> now) shown on every track card, and a "PTZ" badge when a registered camera is near enough to cue on that track.
- The map's bottom Verified/Unverified filter now has one diamond chip per classification actually present in each bucket (not just a flat verified/unverified toggle), each independently togglable, plus an ALL reset per row.
- `Track.risk_score` (`app/risk.py`): a plain, fully-documented 0-10 point score from classification + verified + the worst open incident's severity -- not a proprietary/opaque ML ranking this app has no trained model to back. Surfaced on every track card and as a new "Risk: highest first" dashboard sort option.
- The after-action report (`GET /api/incidents/{id}/report`) now includes an `identification` section: real per-aircraft identity fragments a sensor already decoded (DJI DroneID `serial_number`, ASTERIX radar's Mode S `aircraft_address`/`callsign`/squawk, ASTM Remote ID `operator_id`) that were previously captured in `raw_data` but never surfaced in the report -- not a manufacturer/model lookup this app has no data to back. Also now includes the track's `aircraft_category` when known.
- Two more RF signatures (`app/rf_signatures.py`): ExpressLRS/TBS Crossfire-style long-range RC control links at both 900 MHz and 2.4 GHz, distinguished from the existing video-link entries by their much narrower channel width -- a real sub-1 GHz coverage gap the signature table didn't have before, from published open-source (ExpressLRS) and public (TBS Crossfire) specs.
- A real connection-status dot in the topbar (`updateConnectionChip()`) -- was previously a hardcoded, always-green "live" indicator regardless of whether the WebSocket push or even the polling fallback was actually working. Now reflects connected (push live) / degraded (push down, polling still succeeding) / unavailable (the last poll itself failed).
- A "coasting" display state for a track that's gone quiet but hasn't yet been marked lost server-side: its map marker dims and gets a dashed dead-reckoning line + "estimated position" tooltip extrapolated from its last known heading/speed, and its list card shows "coasting" instead of implying the plotted position is still fresh.
- `Track.risk_score` now also factors in proximity to the nearest active restricted zone (`app.zones.nearest_restricted_zone_distance_m`) -- a track closing in on a protected zone scores higher even before it actually enters one and an incident opens.
- A confirmation prompt before marking a track as Foe -- the highest-consequence operator override, since it can escalate an incident's severity.
- Timed Ignore: `POST /api/tracks/{id}/ignore` now accepts `duration_minutes` (5/30/Indefinitely in the dashboard) -- a timed ignore expires and reverts to normal alerting on its own (`Track.ignored_until`, resolved at read time in `app.db._row_to_track`, no separate sweep job needed).
- A real notification bell in the topbar (`detectAndRecordNotifications()`): a locally-observed log of genuine state transitions this dashboard itself sees between polls (a new incident opening, a sensor's health getting worse, a track going active -> lost) -- not a fabricated feed, and not persisted server-side.
- The Sensor Health panel now shows each sensor's registered position (or "not registered") alongside its health status.
- `Track.risk_factors` (`app.risk.assess_risk`, the same function that now also computes `risk_score`) and `Track.zone_status` ("inside"/"approaching"/"none", reusing the exact zone-containment and proximity checks the risk score's proximity term already uses): a risk-explanation panel in the details panel's Quality tab lists each factor that actually contributed to the score in plain English, and a subtitle-row badge shows zone status alongside a confidence tier ("Low"/"Moderate"/"High", a plain relabeling of `corroborating_sensor_types` -- not a new score). None of this is a new metric; it's the existing score and existing corroboration/zone data made explainable at a glance.
- A Timeline tab in the details panel, built from data every viewer already has access to (a track's first-seen time and its own zone-incursion incidents opening/closing), with admin-only classification-change entries best-effort-appended from the existing admin-only `GET /api/audit-log` -- deliberately not a fabricated camera-tracking event log.
- An honest 3-state camera-state label (`#camera-state-label`) on the Live view tab -- "connecting", "Live", or "Camera unreachable" -- reporting only what this app can actually observe about the MJPEG feed, explicitly not a closed-loop auto-tracking state machine this app has no visual tracking behind.

## [0.2.0] - 2026-08-31

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
- WiFi Beacon Remote ID reception (`app/adapters/astm_remote_id_wifi_bridge.py`): ASTM F3411's other broadcast transport, alongside the existing BLE bridge -- a drone transmitting only over WiFi was previously invisible to this app.
- Acoustic classification scaffolding (`app/acoustic_features.py`'s MFCC extraction, `app/ml/train_acoustic.py`, `app/ml/acoustic_model.py`): `acoustic_array_bridge.py` can now optionally consult a trained classifier's opinion of the actual rotor/propeller acoustic signature instead of always reporting a flat, manually-estimated confidence -- same "ships no trained model" scaffolding-only posture as the existing `app/ml/` classifier.
- Meshtastic off-grid alerting (`app/alerting.py`'s `notify_meshtastic`): a severity-routed alert channel over a Meshtastic node's LoRa mesh, for a deployment with no internet/cell connectivity at all -- the scenario every other alert channel (Slack, PagerDuty, SMS) assumes away.
- Generic RF energy-detection sweep (`app/adapters/rf_sweep_bridge.py`): reads `hackrf_sweep`'s CSV output and flags any bin exceeding the noise floor by a configurable margin, independent of protocol -- catches an unknown or non-cooperative RF emitter none of the protocol-specific RF adapters would recognize.
- Esri Topo added as a fourth selectable map base layer, alongside the existing Dark/Road/Satellite.
- FAA Class Airspace import (`app/airspace/faa_class_airspace.py`): Class B/C/D/E controlled-airspace surface areas as zones, via `app/adapters/faa_zones_import.py --source class-airspace` -- the permanent airspace structure, distinct from the existing UAS Facility Map (altitude ceilings) and NOTAM (temporary) sources.
- FAA Special Use Airspace import (`app/airspace/faa_special_use_airspace.py`): Prohibited/Restricted/Warning/Alert/Military Operations/National Security Areas as zones, via `--source special-use` -- Prohibited/Restricted import as `no_fly`, the most directly relevant of the four FAA sources for a drone-detection deployment.

### Fixed

- Startup crash on a fresh checkout: the SQLite data directory wasn't created before the app tried to open its database file there.
- Zone name uniqueness was check-then-act (a TOCTOU race under concurrent creates); now enforced with a database-level unique index, translated to a 409 on conflict.
- `update_incident` silently dropped a changed `severity` field.
- SQLite now runs in WAL mode (was DELETE mode), and the dashboard surfaces non-auth API errors (e.g. a failed acknowledge) as a toast instead of only logging to the console.
- `acoustic_array_bridge.py` now validates `--mic-positions`/`--assumed-range-m` upfront and surfaces a POST failure's actual error body, instead of failing deep inside a beamforming call or printing an unhelpful HTTP status line.
- `faa_special_use_airspace.py`'s Prohibited/Restricted classification only checked the `NAME` field for the "P-"/"R-" designator prefix; a realistic response carrying the designator in `SUAS_IDENT` instead (with a longer descriptive `NAME`) would have silently landed as `monitoring` instead of `no_fly`. Now checks both fields.

### Security

None of this release's three new FAA ArcGIS airspace imports (Class Airspace, Special Use Airspace) or the pre-existing UAS Facility Map/NOTAM integrations, nor `astm_remote_id_wifi_bridge.py`, `dji_droneid_bridge.py`, `rf_sweep_bridge.py`, or Meshtastic alerting, were validated against a live endpoint, real hardware, or a live account in the environment this release was built in -- outbound network access there was restricted to a small allowlist that excluded every FAA/ArcGIS/aviation domain. Field names and query/protocol mechanics are confirmed from each source's own published documentation where possible; sanity-check your first real use of any of these against a known reference before relying on it for anything safety-relevant. See each module's own docstring for specifics.

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
