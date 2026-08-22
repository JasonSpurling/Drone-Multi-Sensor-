"""Publishes AIS vessel position reports to Anduril Lattice as Entities --
see ais.py for the field mapping. Matches Anduril's own "Integrate
maritime AIS position data" sample app: models each vessel as an entity
with a location that's periodically updated, so it can be visualized on
Lattice's common operating picture alongside this repo's own drone tracks.

Standalone from the rest of this repo: reads AIS position reports from a
JSON-lines file (one decoded record per line -- see ais.py's module
docstring for the shape and sample_vessels.jsonl for a bundled fixture
with fabricated data), not from this app's own detection/tracking
pipeline. A real deployment would point this at a live AIS
receiver/decoder's output instead of a static file.

Usage:
    pip install -r requirements-lattice.txt
    export LATTICE_ENDPOINT=lattice-your_env_id.env.sandboxes.developer.anduril.com
    export LATTICE_CLIENT_ID=...
    export LATTICE_CLIENT_SECRET=...
    export SANDBOXES_TOKEN=...          # only needed for Lattice Sandboxes

    python samples/lattice/ais/ais_publisher.py samples/lattice/ais/sample_vessels.jsonl
    python samples/lattice/ais/ais_publisher.py samples/lattice/ais/sample_vessels.jsonl --loop --interval 30
"""

from __future__ import annotations

import argparse
import json
import os
import time

from ais import ais_record_to_publish_entity_kwargs, parse_ais_record


def _load_records(path: str) -> list[dict]:
    records = []
    with open(path) as file:
        for line_number, line in enumerate(file, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(parse_ais_record(json.loads(line)))
            except (json.JSONDecodeError, ValueError) as exc:
                raise ValueError(f"{path}:{line_number}: {exc}") from exc
    return records


def _build_sdk_kwargs(kwargs: dict) -> dict:
    from anduril import Aliases, Location, MilView, Ontology, Position, Provenance

    return {
        "entity_id": kwargs["entity_id"],
        "is_live": kwargs["is_live"],
        "expiry_time": kwargs["expiry_time"],
        "aliases": Aliases(**kwargs["aliases"]),
        "location": Location(
            position=Position(**kwargs["location"]["position"]),
            speed_mps=kwargs["location"].get("speed_mps"),
        ),
        "mil_view": MilView(**kwargs["mil_view"]),
        "provenance": Provenance(**kwargs["provenance"]),
        "ontology": Ontology(**kwargs["ontology"]),
    }


def publish_once(client, records: list[dict]) -> None:
    for record in records:
        kwargs = ais_record_to_publish_entity_kwargs(record)
        sdk_kwargs = _build_sdk_kwargs(kwargs)
        client.entities.publish_entity(**sdk_kwargs)
        print(f"-> published {sdk_kwargs['entity_id']} ({record['vessel_name'] or record['mmsi']})")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("records_path", help="JSON-lines file of decoded AIS position reports (see ais.py)")
    parser.add_argument(
        "--lattice-endpoint", default=os.getenv("LATTICE_ENDPOINT"),
        required=not os.getenv("LATTICE_ENDPOINT"),
    )
    parser.add_argument(
        "--lattice-client-id", default=os.getenv("LATTICE_CLIENT_ID"),
        required=not os.getenv("LATTICE_CLIENT_ID"),
    )
    parser.add_argument(
        "--lattice-client-secret", default=os.getenv("LATTICE_CLIENT_SECRET"),
        required=not os.getenv("LATTICE_CLIENT_SECRET"),
    )
    parser.add_argument("--sandboxes-token", default=os.getenv("SANDBOXES_TOKEN", ""))
    parser.add_argument(
        "--loop", action="store_true",
        help="Keep re-publishing the same file every --interval seconds (simulates periodic AIS "
        "updates for a static fixture) instead of publishing once and exiting.",
    )
    parser.add_argument("--interval", type=float, default=30.0)
    args = parser.parse_args()

    from anduril import Lattice

    headers = {}
    if args.sandboxes_token:
        headers["anduril-sandbox-authorization"] = f"Bearer {args.sandboxes_token}"
    client = Lattice(
        base_url=f"https://{args.lattice_endpoint}",
        client_id=args.lattice_client_id,
        client_secret=args.lattice_client_secret,
        headers=headers,
    )

    records = _load_records(args.records_path)
    print(f"Loaded {len(records)} AIS position report(s) from {args.records_path}")

    if not args.loop:
        publish_once(client, records)
        return

    print(f"Re-publishing every {args.interval}s -- Ctrl+C to stop")
    while True:
        publish_once(client, records)
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
