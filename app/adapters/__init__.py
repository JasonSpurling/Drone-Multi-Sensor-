"""Real-sensor bridges: standalone scripts that read a live feed from
actual hardware/infrastructure and POST it to this app's own
/api/detections (or, for the CLI-only ones, just print/import data
directly) -- nothing in app/ imports from this package at request time,
since the app's own API never talks to a sensor directly.

- sdk.py -- shared POST-to-/api/detections + argparse plumbing, factored
  out once real duplication existed across several adapters (see its own
  docstring); camera_motion.py is the first adapter built on it, the
  others aren't all migrated in one pass
- sbs1.py / dump1090_bridge.py -- ADS-B via dump1090's SBS-1 text feed,
  plus ICAO emitter-category enrichment from aircraft.json
- asterix.py / asterix_bridge.py -- radar via ASTERIX CAT048
- camera_motion.py / camera_yolo.py -- OpenCV motion cueing / YOLO
- acoustic_array_bridge.py -- microphone-array bearing estimation
- mavlink.py / mavlink_bridge.py -- MAVLink telemetry (friendly/
  cooperative drones)
- dji_droneid.py / dji_droneid_bridge.py -- DJI DroneID payload decoding
- astm_remote_id.py / astm_remote_id_ble_bridge.py -- ASTM F3411 Remote
  ID over BLE
- onvif_ptz_bridge.py -- ONVIF PTZ camera slew-to-cue execution
- lattice.py / lattice_bridge.py -- Anduril Lattice downstream sync
- faa_notam_check.py / faa_zones_import.py -- one-off FAA airspace-data
  CLIs (see app/airspace/)
- validate_zone.py -- checks a hand-written/drawn zone polygon is
  structurally valid (app/zone_validation.py) before it goes into
  app/zones.seed.json or POST /api/zones; not a sensor bridge like the
  rest of this package, but placed here rather than scripts/ since it
  needs app.models.Zone the same way the FAA CLIs above need app.db

Each `*_bridge.py`/adapter is its own optional dependency (see the
per-integration requirements-*.txt at the repo root) -- a deployment only
installs the drivers/SDKs for the sensors it actually runs.
"""
