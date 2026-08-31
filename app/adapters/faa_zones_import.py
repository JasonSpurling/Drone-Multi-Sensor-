"""One-off CLI to import real FAA airspace zones for a bounding box into
this tracker's database, alongside (or instead of) the hand-seeded
app/zones.seed.json. Three sources, see each module's own docstring for
what the data actually is and the honesty caveat about none being
validated against a live response in the environment this was built in:

    facility-map    app/airspace/faa_uas_facility_map.py -- LAANC
                     pre-authorization altitude ceilings (the default)
    class-airspace   app/airspace/faa_class_airspace.py -- Class B/C/D/E
                     controlled-airspace surface areas
    special-use      app/airspace/faa_special_use_airspace.py -- Prohibited/
                     Restricted/Warning/Alert/MOA/National Security Areas

Usage:
    # A box roughly covering the London area, into site id 1 (see GET
    # /api/sites or this app's startup log for your site's id -- a fresh,
    # not-yet-multi-site deployment has exactly one, the auto-created
    # "default" site):
    .venv/bin/python -m app.adapters.faa_zones_import \\
        --min-lon -0.5 --min-lat 51.3 --max-lon 0.3 --max-lat 51.7 --site-id 1

    .venv/bin/python -m app.adapters.faa_zones_import --source class-airspace \\
        --min-lon -0.5 --min-lat 51.3 --max-lon 0.3 --max-lat 51.7 --site-id 1

    .venv/bin/python -m app.adapters.faa_zones_import --source special-use \\
        --min-lon -0.5 --min-lat 51.3 --max-lon 0.3 --max-lat 51.7 --site-id 1
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import dataclass

from app.airspace.faa_class_airspace import DEFAULT_FEATURE_SERVER_URL as CLASS_AIRSPACE_URL
from app.airspace.faa_class_airspace import import_class_airspace_zones
from app.airspace.faa_special_use_airspace import DEFAULT_FEATURE_SERVER_URL as SPECIAL_USE_URL
from app.airspace.faa_special_use_airspace import import_special_use_airspace_zones
from app.airspace.faa_uas_facility_map import DEFAULT_FEATURE_SERVER_URL as FACILITY_MAP_URL
from app.airspace.faa_uas_facility_map import import_facility_map_zones
from app.models import Zone


@dataclass(frozen=True)
class _Source:
    default_url: str
    label: str
    importer: Callable[[float, float, float, float, int, str], list[Zone]]


_SOURCES = {
    "facility-map": _Source(FACILITY_MAP_URL, "FAA UAS Facility Map", import_facility_map_zones),
    "class-airspace": _Source(CLASS_AIRSPACE_URL, "FAA Class Airspace", import_class_airspace_zones),
    "special-use": _Source(SPECIAL_USE_URL, "FAA Special Use Airspace", import_special_use_airspace_zones),
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--min-lon", type=float, required=True)
    parser.add_argument("--min-lat", type=float, required=True)
    parser.add_argument("--max-lon", type=float, required=True)
    parser.add_argument("--max-lat", type=float, required=True)
    parser.add_argument("--site-id", type=int, required=True)
    parser.add_argument("--source", choices=sorted(_SOURCES), default="facility-map")
    parser.add_argument(
        "--feature-server-url", default=None,
        help="Defaults to the standard endpoint for --source; override if the FAA has moved it",
    )
    args = parser.parse_args()

    source = _SOURCES[args.source]
    feature_server_url = args.feature_server_url or source.default_url
    zones = source.importer(args.min_lon, args.min_lat, args.max_lon, args.max_lat, args.site_id, feature_server_url)
    print(f"Imported {len(zones)} {source.label} zone(s).")


if __name__ == "__main__":
    main()
