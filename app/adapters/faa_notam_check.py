"""CLI to fetch active FAA NOTAMs near a point -- prints them for
situational awareness by default, or persists each as an approximate
circular zone (see app/airspace/faa_notam.py's docstring for why it's a
search-circle approximation, not the NOTAM's real footprint) when
--site-id is given. See app/airspace/faa_notam.py for the important
caveat that it wasn't validated against a live api.faa.gov account in
the environment this was built in -- verify it works against your own
credentials first.

Usage:
    # Print only (unchanged from before):
    .venv/bin/python -m app.adapters.faa_notam_check \\
        --client-id "$DRONE_FAA_NOTAM_CLIENT_ID" --client-secret "$DRONE_FAA_NOTAM_CLIENT_SECRET" \\
        --lat 51.5 --lon -0.1 --radius-nm 50

    # Also import each as a zone into site id 1 (see GET /api/sites or
    # this app's startup log for your site's id) -- safe to re-run
    # periodically (e.g. from cron), skips a NOTAM already imported:
    .venv/bin/python -m app.adapters.faa_notam_check \\
        --client-id "$DRONE_FAA_NOTAM_CLIENT_ID" --client-secret "$DRONE_FAA_NOTAM_CLIENT_SECRET" \\
        --lat 51.5 --lon -0.1 --radius-nm 50 --site-id 1
"""

from __future__ import annotations

import argparse

from app.airspace.faa_notam import fetch_notams, import_notams_as_zones
from app.config import FAA_NOTAM_CLIENT_ID, FAA_NOTAM_CLIENT_SECRET


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--client-id", default=FAA_NOTAM_CLIENT_ID)
    parser.add_argument("--client-secret", default=FAA_NOTAM_CLIENT_SECRET)
    parser.add_argument("--lat", type=float, required=True)
    parser.add_argument("--lon", type=float, required=True)
    parser.add_argument("--radius-nm", type=float, default=100.0)
    parser.add_argument(
        "--site-id", type=int, default=None,
        help="If given, also import each NOTAM as an approximate circular zone into this site "
        "(in addition to printing them). Omit to only print, unchanged from before.",
    )
    args = parser.parse_args()

    if not args.client_id or not args.client_secret:
        raise SystemExit("Both --client-id and --client-secret are required (register at api.faa.gov).")

    if args.site_id is not None:
        zones = import_notams_as_zones(
            args.client_id, args.client_secret, args.lat, args.lon, args.site_id, args.radius_nm
        )
        print(f"Imported {len(zones)} NOTAM zone(s) (approximate search-circle boundaries).")
        return

    notams = fetch_notams(args.client_id, args.client_secret, args.lat, args.lon, args.radius_nm)
    print(f"{len(notams)} active NOTAM(s) within {args.radius_nm:.0f}nm:")
    for notam in notams:
        print(f"- [{notam['number']}] {notam['icao_location']}: {notam['text']}")


if __name__ == "__main__":  # pragma: no cover -- only executes when this
    # file is run directly as a script (python -m / a shebang), never when
    # imported under pytest, so it is structurally unreachable in-process;
    # main()'s own body is covered by tests that call it directly.
    main()
