"""Entity visualizer -- a simple web app showing every entity in a Lattice
environment on a map, matching Anduril's own "Entity visualizer" sample
app. Standalone from the rest of this repo: it renders entities pulled
FROM Lattice (any source publishing to that environment, not just this
repo's own tracker), which is a different thing from this app's own
dashboard.html (which shows this app's own tracks, from its own database).

Architecture: a background thread holds Lattice's `stream_entities` call
open (a long-lived gRPC/HTTP2 stream, reconnecting on drop) and maintains
an in-memory `entity_id -> entity dict` cache, updated on each
EVENT_TYPE_UPDATE/CREATED/PREEXISTING and pruned on EVENT_TYPE_DELETED.
The (stdlib-only, no new dependency) HTTP server serves that cache as
GeoJSON at GET /api/entities and the static map page at GET /. Deliberately
not built on this repo's own FastAPI app -- this is a standalone sample
with no dependency on the rest of the codebase, matching how Anduril's own
sample apps are each independent programs.

Usage:
    pip install -r requirements-lattice.txt
    export LATTICE_ENDPOINT=lattice-your_env_id.env.sandboxes.developer.anduril.com
    export LATTICE_CLIENT_ID=...
    export LATTICE_CLIENT_SECRET=...
    export SANDBOXES_TOKEN=...          # only needed for Lattice Sandboxes

    python samples/lattice/entity_visualizer/server.py
    # then open http://127.0.0.1:8090
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from entities import entities_to_feature_collection

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"

# Guards _entities against concurrent access: the streaming thread writes
# on every event, the HTTP server's (possibly several, ThreadingHTTPServer)
# request-handling threads read on every GET /api/entities.
_lock = threading.Lock()
_entities: dict[str, dict] = {}


def _stream_loop(client, reconnect_delay_s: float) -> None:
    """Runs forever, reconnecting after a delay if the stream drops --
    stream_entities is a long-lived call that can end on its own (network
    blip, server-side restart), and a visualizer that silently stops
    updating on the first disconnect defeats its own purpose.
    """
    while True:
        try:
            logger.info("Connecting to Lattice entity stream...")
            for event in client.entities.stream_entities(pre_existing_only=False):
                if event.event == "heartbeat":
                    continue
                entity_dict = event.entity.model_dump(by_alias=True, exclude_none=True)
                entity_id = entity_dict.get("entityId")
                if entity_id is None:
                    continue
                with _lock:
                    if event.event_type == "EVENT_TYPE_DELETED":
                        _entities.pop(entity_id, None)
                    else:
                        _entities[entity_id] = entity_dict
        except Exception:
            logger.exception("Entity stream disconnected -- reconnecting in %.0fs", reconnect_delay_s)
            time.sleep(reconnect_delay_s)


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        logger.info("%s - %s", self.address_string(), fmt % args)

    def do_GET(self) -> None:
        if self.path == "/api/entities":
            with _lock:
                body = json.dumps(entities_to_feature_collection(_entities)).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        requested = "/index.html" if self.path in ("/", "") else self.path
        file_path = (STATIC_DIR / requested.lstrip("/")).resolve()
        if STATIC_DIR not in file_path.parents or not file_path.is_file():
            self.send_error(404)
            return
        content_type = "text/html" if file_path.suffix == ".html" else "application/octet-stream"
        body = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
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
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument("--reconnect-delay", type=float, default=5.0)
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

    stream_thread = threading.Thread(
        target=_stream_loop, args=(client, args.reconnect_delay), daemon=True, name="entity-stream"
    )
    stream_thread.start()

    server = ThreadingHTTPServer((args.host, args.port), _Handler)
    print(f"Entity visualizer running at http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()


if __name__ == "__main__":
    main()
