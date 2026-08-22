"""Generates a new API key in the shape app/auth.py expects, and (given a
DRONE_API_KEYS_FILE-style JSON file) can add or remove it in place --
letting a key be rotated without restarting the app.

Rotation without downtime works because app.auth.configured_keys() (via
app.config.get_api_key()/get_api_keys_json(), see that module) re-reads
DRONE_API_KEYS_FILE's target file fresh on every authenticated request --
there's no separate "reload config" step to trigger. The safe rotation
procedure this script is built around:

  1. Generate a new key, add it to the file *alongside* the old one (an
     overlap window) -- both now work.
  2. Update whatever's using the old key (a sensor's config, an
     operator's saved key, ...) to the new one.
  3. Once nothing authenticates with the old key any more (check
     GET /api/admin/keys' last_used_at/use_count for it), remove it from
     the file.

Usage:
    # One-off: print a new key + its DRONE_API_KEYS-shaped JSON entry,
    # without touching any file (for a deployment using the plain
    # DRONE_API_KEYS env var, where you'd paste this in and restart --
    # the *_FILE-based hot-rotation path below needs no restart).
    python scripts/rotate_api_key.py generate --role ingest --label radar-1

    # Add a newly generated key to a DRONE_API_KEYS_FILE-style JSON file
    # in place (creates the file with just this key if it doesn't exist
    # yet):
    python scripts/rotate_api_key.py add /run/secrets/drone_api_keys.json \\
        --role ingest --label radar-1

    # Remove a key from that file once nothing's using it any more --
    # takes effect on the very next request, no restart:
    python scripts/rotate_api_key.py remove /run/secrets/drone_api_keys.json \\
        --key <the-old-key>
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys
from pathlib import Path

# 32 bytes -> 43 url-safe base64 characters, the same order of magnitude
# of entropy as the hand-picked example keys already scattered through
# this repo's own docs/tests (e.g. "key-for-site-a-0123456789abcdef")
# but actually random rather than a readable fixture.
_KEY_BYTES = 32


def generate_key() -> str:
    return secrets.token_urlsafe(_KEY_BYTES)


def _build_entry(role: str, site: str | None, label: str | None) -> dict | str:
    """A bare role string if no site/label was given (matches the
    pre-multi-site DRONE_API_KEYS shape, still supported) -- only becomes
    the full object shape once there's a reason to.
    """
    if site is None and label is None:
        return role
    entry: dict = {"role": role}
    if site is not None:
        entry["site"] = site
    if label is not None:
        entry["label"] = label
    return entry


def cmd_generate(args: argparse.Namespace) -> int:
    key = generate_key()
    entry = _build_entry(args.role, args.site, args.label)
    print(f"New key: {key}")
    print("DRONE_API_KEYS entry:")
    print(json.dumps({key: entry}, indent=2))
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    path = Path(args.keys_file)
    keys = json.loads(path.read_text()) if path.is_file() else {}

    key = generate_key()
    keys[key] = _build_entry(args.role, args.site, args.label)
    path.write_text(json.dumps(keys, indent=2) + "\n")

    print(f"New key: {key}")
    print(f"Added to {path} ({len(keys)} key(s) now in the file).")
    print(
        "Takes effect on the app's very next request against a "
        "DRONE_API_KEYS_FILE pointed at this file -- no restart needed."
    )
    return 0


def cmd_remove(args: argparse.Namespace) -> int:
    path = Path(args.keys_file)
    if not path.is_file():
        print(f"No such file: {path}", file=sys.stderr)
        return 1
    keys = json.loads(path.read_text())
    if args.key not in keys:
        print(f"Key not found in {path} (nothing to do).")
        return 0
    del keys[args.key]
    path.write_text(json.dumps(keys, indent=2) + "\n")
    print(f"Removed. {len(keys)} key(s) remain in {path}.")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_generate = subparsers.add_parser("generate", help="Print a new key, no file involved")
    p_generate.add_argument("--role", required=True, choices=["ingest", "viewer", "operator", "admin"])
    p_generate.add_argument("--site", default=None, help="Site name to scope this key to (default site if omitted)")
    p_generate.add_argument("--label", default=None, help="Human-readable label for GET /api/admin/keys")
    p_generate.set_defaults(func=cmd_generate)

    p_add = subparsers.add_parser("add", help="Generate a new key and add it to a DRONE_API_KEYS_FILE-style JSON file")
    p_add.add_argument("keys_file")
    p_add.add_argument("--role", required=True, choices=["ingest", "viewer", "operator", "admin"])
    p_add.add_argument("--site", default=None)
    p_add.add_argument("--label", default=None)
    p_add.set_defaults(func=cmd_add)

    p_remove = subparsers.add_parser("remove", help="Remove a key from a DRONE_API_KEYS_FILE-style JSON file")
    p_remove.add_argument("keys_file")
    p_remove.add_argument("--key", required=True)
    p_remove.set_defaults(func=cmd_remove)

    args = parser.parse_args()
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()
