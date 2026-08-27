"""One-off CLI to print active FAA NOTAMs near a point, for situational
awareness alongside this tracker's zones. See
app/airspace/faa_notam.py for what this is and the important caveat that
it wasn't validated against a live api.faa.gov account in the environment
this was built in -- verify it works against your own credentials first.

Usage:
    .venv/bin/python -m app.adapters.faa_notam_check \\
        --client-id "$DRONE_FAA_NOTAM_CLIENT_ID" --client-secret "$DRONE_FAA_NOTAM_CLIENT_SECRET" \\
        --lat 51.5 --lon -0.1 --radius-nm 50
"""

from __future__ import annotations

import argparse

from app.airspace.faa_notam import fetch_notams
from app.config import FAA_NOTAM_CLIENT_ID, FAA_NOTAM_CLIENT_SECRET


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--client-id", default=FAA_NOTAM_CLIENT_ID)
    parser.add_argument("--client-secret", default=FAA_NOTAM_CLIENT_SECRET)
    parser.add_argument("--lat", type=float, required=True)
    parser.add_argument("--lon", type=float, required=True)
    parser.add_argument("--radius-nm", type=float, default=100.0)
    args = parser.parse_args()

    if not args.client_id or not args.client_secret:
        raise SystemExit("Both --client-id and --client-secret are required (register at api.faa.gov).")

    notams = fetch_notams(args.client_id, args.client_secret, args.lat, args.lon, args.radius_nm)
    print(f"{len(notams)} active NOTAM(s) within {args.radius_nm:.0f}nm:")
    for notam in notams:
        print(f"- [{notam['number']}] {notam['icao_location']}: {notam['text']}")


if __name__ == "__main__":
    main()
