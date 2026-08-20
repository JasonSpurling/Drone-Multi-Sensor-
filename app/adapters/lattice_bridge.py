"""Publishes this tracker's active tracks to an Anduril Lattice deployment
as Entities, via the Lattice SDK's Entities API -- see app/adapters/lattice.py
for the field mapping. The reverse direction of every other adapter in this
package: this pushes OUR fused output out, rather than bringing a sensor's
raw signal in.

Optionally attaches a thumbnail image to each published entity via the
Objects API, if the track's most recent camera detection carries a
raw_data.snapshot_path (see camera_yolo.py's --snapshot-dir) -- the same
override_entity + Media pattern as Anduril's own sample-app-thumbnail.

Usage:
    pip install -r requirements-lattice.txt
    export LATTICE_ENDPOINT=lattice-your_env_id.env.sandboxes.developer.anduril.com
    export LATTICE_CLIENT_ID=...
    export LATTICE_CLIENT_SECRET=...
    export SANDBOXES_TOKEN=...          # only needed for Lattice Sandboxes
    python -m app.adapters.lattice_bridge --api-url http://127.0.0.1:8000/api
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
import urllib.error
import urllib.request

from app.adapters.lattice import track_to_publish_entity_kwargs

logger = logging.getLogger(__name__)


def _get_json(url: str, api_key: str = "") -> list[dict]:
    headers = {"X-API-Key": api_key} if api_key else {}
    request = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(request, timeout=5) as response:
        result: list[dict] = json.loads(response.read())
        return result


def _fetch_active_tracks(api_url: str, api_key: str) -> list[dict]:
    return _get_json(f"{api_url}/tracks?status=active&limit=1000", api_key)


def _latest_camera_snapshot_path(api_url: str, api_key: str, track_id: int) -> str | None:
    detections = _get_json(f"{api_url}/tracks/{track_id}/history", api_key)
    for detection in reversed(detections):
        raw_data = detection.get("raw_data") or {}
        if "snapshot_path" in raw_data:
            path = raw_data["snapshot_path"]
            return path if os.path.isfile(path) else None
    return None


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


def _attach_thumbnail(client, entity_id: str, snapshot_path: str, uploaded: set[str]) -> None:
    """Uploads `snapshot_path` as a Lattice Object and links it to
    `entity_id`'s media component, skipping paths already uploaded this
    process's lifetime (a track's snapshot doesn't change every poll cycle).
    """
    from anduril import Entity, Media, MediaItem, Provenance

    if snapshot_path in uploaded:
        return
    object_path = os.path.basename(snapshot_path)
    with open(snapshot_path, "rb") as file:
        client.objects.upload_object(object_path=object_path, request=file)
    client.entities.override_entity(
        entity_id=entity_id,
        field_path="media.media",
        entity=Entity(
            entity_id=entity_id,
            media=Media(media=[MediaItem(relative_path=object_path, type="MEDIA_TYPE_IMAGE")]),
        ),
        provenance=Provenance(integration_name="drone-multi-sensor", data_type="track_thumbnail"),
    )
    uploaded.add(snapshot_path)


def watch(args: argparse.Namespace) -> None:
    from anduril import Lattice

    from app.models import Track

    headers = {}
    if args.sandboxes_token:
        headers["anduril-sandbox-authorization"] = f"Bearer {args.sandboxes_token}"
    client = Lattice(
        base_url=f"https://{args.lattice_endpoint}",
        client_id=args.lattice_client_id,
        client_secret=args.lattice_client_secret,
        headers=headers,
    )

    uploaded_snapshots: set[str] = set()
    print(f"Publishing active tracks from {args.api_url} to {args.lattice_endpoint} every {args.poll_interval}s ...")
    while True:
        try:
            raw_tracks = _fetch_active_tracks(args.api_url, args.api_key)
            for raw_track in raw_tracks:
                track = Track.model_validate(raw_track)
                kwargs = track_to_publish_entity_kwargs(track)
                if kwargs is None:
                    continue
                sdk_kwargs = _build_sdk_kwargs(kwargs)
                client.entities.publish_entity(**sdk_kwargs)
                print(f"-> published {sdk_kwargs['entity_id']}")

                if args.attach_thumbnails and track.id is not None:
                    snapshot_path = _latest_camera_snapshot_path(args.api_url, args.api_key, track.id)
                    if snapshot_path is not None:
                        _attach_thumbnail(client, sdk_kwargs["entity_id"], snapshot_path, uploaded_snapshots)
        except urllib.error.URLError as exc:
            logger.error("Error fetching tracks from %s: %s", args.api_url, exc)
        except Exception:
            logger.exception("Error publishing to Lattice")
        time.sleep(args.poll_interval)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--api-url", default="http://127.0.0.1:8000/api", help="This app's own API base URL")
    parser.add_argument("--api-key", default=os.getenv("DRONE_API_KEY", ""))
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
    parser.add_argument("--poll-interval", type=float, default=5.0)
    parser.add_argument(
        "--attach-thumbnails", action="store_true",
        help="Upload each track's most recent camera snapshot (see camera_yolo.py --snapshot-dir) "
        "as a Lattice Object and link it to the entity's media component.",
    )
    args = parser.parse_args()
    watch(args)


if __name__ == "__main__":
    main()
