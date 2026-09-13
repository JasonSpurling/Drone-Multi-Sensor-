"""Validates a zone polygon before it goes into app/zones.seed.json (or a
POST /api/zones body) -- right now a hand-edited/drawn polygon has zero
structural validation: Zone.polygon only checks each vertex is a float
pair, nothing about the shape those vertices actually form. A malformed
polygon (too few vertices, a self-intersecting "bowtie") doesn't raise an
error; app.zones.point_in_polygon's ray-casting test just silently gives
a wrong inside/outside answer near the problem, which is much harder to
notice than an upfront rejection. See app/zone_validation.py for what's
actually checked.

Usage:
    # Check a new zone's polygon, print pass/fail, don't touch any file:
    .venv/bin/python -m app.adapters.validate_zone check \\
        --polygon '[[51.49,-0.11],[51.49,-0.09],[51.51,-0.09],[51.51,-0.11]]'

    # Same check, then append it to app/zones.seed.json if valid:
    .venv/bin/python -m app.adapters.validate_zone add \\
        --name "New Restricted Zone" --zone-type restricted \\
        --polygon '[[51.49,-0.11],[51.49,-0.09],[51.51,-0.09],[51.51,-0.11]]' \\
        --max-altitude-m 120 --write app/zones.seed.json

    # Validate every zone already in a seed file (catches a polygon that
    # was hand-edited badly after the fact, not just a new one):
    .venv/bin/python -m app.adapters.validate_zone check-file app/zones.seed.json
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

from pydantic import ValidationError

from app.models import Zone
from app.zone_validation import validate_polygon


def _parse_polygon(raw: str) -> list[tuple[float, float]]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"--polygon is not valid JSON: {exc}") from exc
    if not isinstance(parsed, list):
        raise SystemExit("--polygon must be a JSON list of [lat, lon] pairs")
    try:
        return [(float(pair[0]), float(pair[1])) for pair in parsed]
    except (TypeError, IndexError, ValueError) as exc:
        raise SystemExit(f"--polygon entries must each be a [lat, lon] pair: {exc}") from exc


def _print_result(name: str, errors: list[str]) -> bool:
    if not errors:
        print(f"OK: '{name}' is a structurally valid polygon.")
        return True
    print(f"INVALID: '{name}' has {len(errors)} problem(s):")
    for error in errors:
        print(f"  - {error}")
    return False


def cmd_check(args: argparse.Namespace) -> None:
    polygon = _parse_polygon(args.polygon)
    if not _print_result(args.name or "(unnamed)", validate_polygon(polygon)):
        raise SystemExit(1)


def cmd_check_file(args: argparse.Namespace) -> None:
    path = Path(args.path)
    if not path.exists():
        raise SystemExit(f"File not found: {path}")
    try:
        entries = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{path} is not valid JSON: {exc}") from exc

    all_ok = True
    for i, entry in enumerate(entries):
        name = entry.get("name", f"entry #{i}")
        polygon = [(float(v[0]), float(v[1])) for v in entry.get("polygon", [])]
        if not _print_result(name, validate_polygon(polygon)):
            all_ok = False
    if not entries:
        print(f"{path} has no zones.")
    if not all_ok:
        raise SystemExit(1)


def _write_zone_entries(path: Path, entries: list[dict]) -> None:
    # Same atomic temp-file-then-rename swap as scripts/rotate_api_key.py's
    # _write_keys_file -- app.zones.load_zones_from_file reads this file
    # fresh on every startup, so a crash mid-write must never leave it
    # truncated or half-written.
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(entries, indent=2) + "\n")
        os.replace(tmp_path, path)
    except BaseException:
        os.unlink(tmp_path)
        raise


def cmd_add(args: argparse.Namespace) -> None:
    polygon = _parse_polygon(args.polygon)
    if not _print_result(args.name, validate_polygon(polygon)):
        raise SystemExit(1)

    # Also run it through the real Zone model (catches an invalid
    # zone_type, a negative altitude band, etc.) -- the same validation
    # app.zones.load_zones_from_file itself applies at startup, so a zone
    # that passes here is guaranteed to actually load, not just pass the
    # geometry check.
    try:
        zone = Zone(
            site_id=None, name=args.name, zone_type=args.zone_type, polygon=polygon,
            min_altitude_m=args.min_altitude_m, max_altitude_m=args.max_altitude_m, active=True,
        )
    except ValidationError as exc:
        print(f"INVALID: '{args.name}' failed full zone validation:\n{exc}")
        raise SystemExit(1) from exc

    if args.write is None:
        print(f"'{args.name}' is valid. Pass --write PATH to append it to a seed file.")
        return

    path = Path(args.write)
    entries = json.loads(path.read_text()) if path.exists() else []
    if any(e.get("name") == args.name for e in entries):
        raise SystemExit(
            f"'{args.name}' already exists in {path} -- edit it there directly, or pick a different name."
        )
    entries.append(
        {
            "name": zone.name,
            "zone_type": zone.zone_type.value,
            "polygon": [list(v) for v in zone.polygon],
            "min_altitude_m": zone.min_altitude_m,
            "max_altitude_m": zone.max_altitude_m,
            "active": zone.active,
        }
    )
    _write_zone_entries(path, entries)
    print(f"Wrote '{args.name}' to {path} ({len(entries)} zone(s) total).")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_check = subparsers.add_parser("check", help="Validate a polygon without touching any file")
    p_check.add_argument("--name", default=None, help="Just for the printed report; not required")
    p_check.add_argument("--polygon", required=True, help="JSON list of [lat, lon] pairs")
    p_check.set_defaults(func=cmd_check)

    p_check_file = subparsers.add_parser("check-file", help="Validate every zone already in a seed file")
    p_check_file.add_argument("path", help="Path to a zones.seed.json-shaped file")
    p_check_file.set_defaults(func=cmd_check_file)

    p_add = subparsers.add_parser("add", help="Validate a new zone, optionally appending it to a seed file")
    p_add.add_argument("--name", required=True)
    p_add.add_argument("--zone-type", required=True, choices=["restricted", "no_fly", "monitoring", "safe"])
    p_add.add_argument("--polygon", required=True, help="JSON list of [lat, lon] pairs")
    p_add.add_argument("--min-altitude-m", type=float, default=None)
    p_add.add_argument("--max-altitude-m", type=float, default=None)
    p_add.add_argument("--write", default=None, help="Append to this seed file if valid (e.g. app/zones.seed.json)")
    p_add.set_defaults(func=cmd_add)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":  # pragma: no cover -- only executes when this
    # file is run directly as a script (python -m / a shebang), never when
    # imported under pytest, so it is structurally unreachable in-process;
    # main()'s own body is covered by tests that call it directly.
    main()
