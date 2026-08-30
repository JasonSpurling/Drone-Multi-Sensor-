"""Airspace-restriction data sourced from the FAA, as an alternative/
supplement to the single hand-seeded zone in app/zones.seed.json:

- faa_uas_facility_map.py -- the LAANC pre-authorization altitude grid
  (ceiling by location, near airports), imported as zones once via
  app/adapters/faa_zones_import.py.
- faa_notam.py -- live NOTAM/TFR lookups (temporary flight restrictions,
  event-driven UAS closures) that a static zone file can't represent,
  queried on demand via app/adapters/faa_notam_check.py.

Both talk to real FAA endpoints and require FAA API credentials
(DRONE_FAA_NOTAM_CLIENT_ID/SECRET) -- neither is wired into the main
detection/tracking pipeline automatically, since airspace data updates on
its own schedule, not per-detection.

IMPORTANT: unlike this tracker's other real integrations, neither module
was validated against a live api.faa.gov account in the environment this
was built in -- verify against your own credentials before relying on it.
"""
