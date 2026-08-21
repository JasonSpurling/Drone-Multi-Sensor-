"""One-off CLI to import real FAA UAS Facility Map zones for a bounding box
into this tracker's database, alongside (or instead of) the hand-seeded
app/zones.seed.json. See app/airspace/faa_uas_facility_map.py for what this
data actually is and the honesty caveat about it not being validated
against a live response in the environment this was built in.

Usage:
    # A box roughly covering the London area, into site id 1 (see GET
    # /api/sites or this app's startup log for your site's id -- a fresh,
    # not-yet-multi-site deployment has exactly one, the auto-created
    # "default" site):
    .venv/bin/python -m app.adapters.faa_zones_import \\
        --min-lon -0.5 --min-lat 51.3 --max-lon 0.3 --max-lat 51.7 --site-id 1
"""

from __future__ import annotations

import argparse

from app.airspace.faa_uas_facility_map import DEFAULT_FEATURE_SERVER_URL, import_facility_map_zones


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--min-lon", type=float, required=True)
    parser.add_argument("--min-lat", type=float, required=True)
    parser.add_argument("--max-lon", type=float, required=True)
    parser.add_argument("--max-lat", type=float, required=True)
    parser.add_argument("--site-id", type=int, required=True)
    parser.add_argument("--feature-server-url", default=DEFAULT_FEATURE_SERVER_URL)
    args = parser.parse_args()

    zones = import_facility_map_zones(
        args.min_lon, args.min_lat, args.max_lon, args.max_lat, args.site_id, args.feature_server_url
    )
    print(f"Imported {len(zones)} FAA UAS Facility Map zone(s).")


if __name__ == "__main__":
    main()
