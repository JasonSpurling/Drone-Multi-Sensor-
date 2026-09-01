"""Terminal admin client for a running instance's HTTP API -- for the
field-kit scenario where an operator needs to check tracks/incidents/
sensors or acknowledge/resolve an incident but the dashboard's browser (or
its Leaflet/CDN map tiles) isn't available, and for scripting routine
checks without hand-rolling curl/jq.

Every subcommand is a thin wrapper over one existing API endpoint (see
app/api/tracks.py, app/api/incidents.py, app/api/sensors.py,
app/api/zones.py) -- this tool adds no server-side behavior of its own,
just a terminal-friendly way to call what's already there.

Usage:
    python scripts/drone_cli.py --url http://127.0.0.1:8000 tracks list
    python scripts/drone_cli.py --url http://127.0.0.1:8000 tracks list --status active
    python scripts/drone_cli.py --url http://127.0.0.1:8000 incidents list --status open
    python scripts/drone_cli.py --url http://127.0.0.1:8000 incidents acknowledge 42
    python scripts/drone_cli.py --url http://127.0.0.1:8000 incidents resolve 42
    python scripts/drone_cli.py --url http://127.0.0.1:8000 sensors list
    python scripts/drone_cli.py --url http://127.0.0.1:8000 zones list

Set DRONE_API_KEY (or pass --api-key) if the target server has auth
enabled -- see README.md's "Authentication" section.
"""

from __future__ import annotations

import argparse
import json
import os

import requests


def _request(
    method: str, base_url: str, path: str, api_key: str = "", params: dict | None = None
) -> requests.Response:
    headers = {"X-API-Key": api_key} if api_key else {}
    response = requests.request(method, f"{base_url}{path}", headers=headers, params=params, timeout=10)
    if not response.ok:
        try:
            detail = response.json().get("detail", response.text)
        except (ValueError, AttributeError):
            detail = response.text
        raise SystemExit(f"[{response.status_code}] {detail}")
    return response


def _print_table(rows: list[dict], columns: list[str]) -> None:
    if not rows:
        print("(none)")
        return
    widths = [max(len(col), *(len(str(row.get(col, ""))) for row in rows)) for col in columns]
    header = "  ".join(col.ljust(w) for col, w in zip(columns, widths, strict=True))
    print(header)
    print("  ".join("-" * w for w in widths))
    for row in rows:
        print("  ".join(str(row.get(col, "")).ljust(w) for col, w in zip(columns, widths, strict=True)))


def _output(args: argparse.Namespace, rows: list[dict], columns: list[str]) -> None:
    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        _print_table(rows, columns)


def cmd_tracks_list(args: argparse.Namespace) -> None:
    params = {"limit": args.limit}
    if args.status:
        params["status"] = args.status
    rows = _request("GET", args.url, "/api/tracks", args.api_key, params).json()
    _output(args, rows, ["id", "track_uid", "status", "classification", "latitude", "longitude", "last_seen"])


def cmd_tracks_show(args: argparse.Namespace) -> None:
    row = _request("GET", args.url, f"/api/tracks/{args.track_id}", args.api_key).json()
    print(json.dumps(row, indent=2))


def cmd_incidents_list(args: argparse.Namespace) -> None:
    params = {"limit": args.limit}
    if args.status:
        params["status"] = args.status
    rows = _request("GET", args.url, "/api/incidents", args.api_key, params).json()
    _output(args, rows, ["id", "status", "severity", "incident_type", "track_id", "zone_id", "opened_at"])


def cmd_incidents_acknowledge(args: argparse.Namespace) -> None:
    row = _request("POST", args.url, f"/api/incidents/{args.incident_id}/acknowledge", args.api_key).json()
    print(f"Incident {row['id']} acknowledged (status={row['status']}, by={row['acknowledged_by']})")


def cmd_incidents_resolve(args: argparse.Namespace) -> None:
    row = _request("POST", args.url, f"/api/incidents/{args.incident_id}/resolve", args.api_key).json()
    print(f"Incident {row['id']} resolved (status={row['status']})")


def cmd_sensors_list(args: argparse.Namespace) -> None:
    rows = _request("GET", args.url, "/api/sensors", args.api_key).json()
    _output(args, rows, ["sensor_id", "sensor_type", "status", "last_seen", "detections_last_hour"])


def cmd_zones_list(args: argparse.Namespace) -> None:
    params = {"include_inactive": args.include_inactive}
    rows = _request("GET", args.url, "/api/zones", args.api_key, params).json()
    _output(args, rows, ["id", "name", "zone_type", "active", "min_altitude_m", "max_altitude_m"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default=os.environ.get("DRONE_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--api-key", default=os.environ.get("DRONE_API_KEY", ""))
    parser.add_argument("--json", action="store_true", help="Print raw JSON instead of a table")
    subparsers = parser.add_subparsers(dest="resource", required=True)

    tracks = subparsers.add_parser("tracks", help="Query tracks").add_subparsers(dest="action", required=True)
    p = tracks.add_parser("list")
    p.add_argument("--status", default=None)
    p.add_argument("--limit", type=int, default=100)
    p.set_defaults(func=cmd_tracks_list)
    p = tracks.add_parser("show")
    p.add_argument("track_id", type=int)
    p.set_defaults(func=cmd_tracks_show)

    incidents = subparsers.add_parser("incidents", help="Query/acknowledge/resolve incidents").add_subparsers(
        dest="action", required=True
    )
    p = incidents.add_parser("list")
    p.add_argument("--status", default=None)
    p.add_argument("--limit", type=int, default=100)
    p.set_defaults(func=cmd_incidents_list)
    p = incidents.add_parser("acknowledge")
    p.add_argument("incident_id", type=int)
    p.set_defaults(func=cmd_incidents_acknowledge)
    p = incidents.add_parser("resolve")
    p.add_argument("incident_id", type=int)
    p.set_defaults(func=cmd_incidents_resolve)

    sensors = subparsers.add_parser("sensors", help="Query sensor health").add_subparsers(
        dest="action", required=True
    )
    p = sensors.add_parser("list")
    p.set_defaults(func=cmd_sensors_list)

    zones = subparsers.add_parser("zones", help="Query zones").add_subparsers(dest="action", required=True)
    p = zones.add_parser("list")
    p.add_argument("--include-inactive", action="store_true")
    p.set_defaults(func=cmd_zones_list)

    args = parser.parse_args()
    try:
        args.func(args)
    except requests.RequestException as exc:
        raise SystemExit(f"Request to {args.url} failed: {exc}") from exc


if __name__ == "__main__":
    main()
