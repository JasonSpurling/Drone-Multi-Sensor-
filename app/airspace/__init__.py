"""Airspace-restriction data sourced from the FAA, as an alternative/
supplement to the single hand-seeded zone in app/zones.seed.json:

- faa_uas_facility_map.py -- the LAANC pre-authorization altitude grid
  (ceiling by location, near airports), imported as zones via
  app/adapters/faa_zones_import.py.
- faa_class_airspace.py -- Class B/C/D/E controlled-airspace surface
  areas (the permanent airspace structure on every VFR sectional chart),
  imported as zones via the same app/adapters/faa_zones_import.py
  (--source class-airspace).
- faa_notam.py -- live NOTAM/TFR lookups (temporary flight restrictions,
  event-driven UAS closures) that a static zone file can't represent,
  queried on demand via app/adapters/faa_notam_check.py.

The first two are both unauthenticated, public ArcGIS FeatureServers,
imported once (or periodically re-run) into the database; faa_notam.py
needs FAA API credentials (DRONE_FAA_NOTAM_CLIENT_ID/SECRET). None of the
three is wired into the main detection/tracking pipeline automatically,
since airspace data updates on its own schedule, not per-detection.

IMPORTANT: unlike this tracker's other real integrations, none of these
three modules was validated against a live response in the environment
this was built in -- every FAA/ArcGIS domain was unreachable from this
environment's network. Field names and query mechanics are confirmed
from each source's own published documentation; verify against a real
response before relying on any of them.
"""
