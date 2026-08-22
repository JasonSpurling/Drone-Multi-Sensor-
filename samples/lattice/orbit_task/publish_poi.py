"""Publishes a Track entity to Lattice representing a sensor
point-of-interest -- the first of three programs in this directory
demonstrating automated tasking (see README.md): a location worth
investigating, published the way an actual sensor integration (radar
contact, camera detection, tip from another system) would.

auto_tasker.py watches for entities like this one and automatically
creates an Orbit task against a taskable asset (orbit_asset.py) to send it
to investigate.

Usage:
    pip install -r requirements-lattice.txt
    export LATTICE_ENDPOINT=lattice-your_env_id.env.sandboxes.developer.anduril.com
    export LATTICE_CLIENT_ID=...
    export LATTICE_CLIENT_SECRET=...
    export SANDBOXES_TOKEN=...          # only needed for Lattice Sandboxes

    python samples/lattice/orbit_task/publish_poi.py --lat 51.5 --lon -0.1 --name "Unidentified contact"
"""

from __future__ import annotations

import argparse
import os
import uuid
from datetime import datetime, timedelta

# How long the published point-of-interest stays live before Lattice
# drops it -- generous, since unlike a moving track this doesn't get
# periodically republished by this one-shot script; a real sensor
# integration would republish on its own cadence instead of relying on a
# single long expiry.
POI_EXPIRY_SECONDS = 900


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--lat", type=float, required=True, help="Latitude, decimal degrees")
    parser.add_argument("--lon", type=float, required=True, help="Longitude, decimal degrees")
    parser.add_argument("--name", default="Sensor point of interest")
    parser.add_argument("--entity-id", default=None, help="Defaults to a random poi-<uuid>")
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
    args = parser.parse_args()

    from anduril import Aliases, Lattice, Location, MilView, Ontology, Position, Provenance

    headers = {}
    if args.sandboxes_token:
        headers["anduril-sandbox-authorization"] = f"Bearer {args.sandboxes_token}"
    client = Lattice(
        base_url=f"https://{args.lattice_endpoint}",
        client_id=args.lattice_client_id,
        client_secret=args.lattice_client_secret,
        headers=headers,
    )

    entity_id = args.entity_id or f"poi-{uuid.uuid4()}"
    now = datetime.utcnow()
    client.entities.publish_entity(
        entity_id=entity_id,
        is_live=True,
        expiry_time=now + timedelta(seconds=POI_EXPIRY_SECONDS),
        aliases=Aliases(name=args.name),
        location=Location(position=Position(latitude_degrees=args.lat, longitude_degrees=args.lon)),
        mil_view=MilView(disposition="DISPOSITION_UNKNOWN", environment="ENVIRONMENT_AIR"),
        provenance=Provenance(integration_name="orbit-task-sample", data_type="point_of_interest", source_id=entity_id),
        ontology=Ontology(template="TEMPLATE_SENSOR_POINT_OF_INTEREST"),
    )
    print(f"Published point-of-interest {entity_id} at ({args.lat}, {args.lon})")


if __name__ == "__main__":
    main()
