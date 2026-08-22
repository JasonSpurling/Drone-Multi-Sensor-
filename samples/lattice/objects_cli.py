"""Objects CLI -- a command-line interface for Anduril Lattice's Objects
API (https://developer.anduril.com/reference/rest/objects/list-objects), a
content-delivery-network service for resilient file storage at the edge.

Standalone from the rest of this repo's Lattice integration
(app/adapters/lattice.py, lattice_bridge.py): those publish this tracker's
own Track data as Lattice Entities; this is a general-purpose file
management tool with no connection to tracks/detections at all, matching
Anduril's own "Objects CLI" sample app.

Usage:
    pip install -r requirements-lattice.txt
    export LATTICE_ENDPOINT=lattice-your_env_id.env.sandboxes.developer.anduril.com
    export LATTICE_CLIENT_ID=...
    export LATTICE_CLIENT_SECRET=...
    export SANDBOXES_TOKEN=...          # only needed for Lattice Sandboxes

    python samples/lattice/objects_cli.py upload local/file.png uploaded/file.png
    python samples/lattice/objects_cli.py download uploaded/file.png local/out.png
    python samples/lattice/objects_cli.py metadata uploaded/file.png
    python samples/lattice/objects_cli.py list --prefix uploaded/
    python samples/lattice/objects_cli.py delete uploaded/file.png

TTL: the Objects API reference above documents how to set a time-to-live
on upload, but the exact query/header parameter name isn't reproduced here
without a live endpoint to confirm the wire format against -- guessing one
that silently does nothing would be worse than not offering it. Instead,
`--extra-query key=value` (repeatable) passes arbitrary extra query
parameters through to any operation, so once you've confirmed the real
parameter name from that reference, you can set it without code changes.
"""

from __future__ import annotations

import argparse
import os
import sys


def _client(args: argparse.Namespace):
    from anduril import Lattice

    headers = {}
    if args.sandboxes_token:
        headers["anduril-sandbox-authorization"] = f"Bearer {args.sandboxes_token}"
    return Lattice(
        base_url=f"https://{args.lattice_endpoint}",
        client_id=args.lattice_client_id,
        client_secret=args.lattice_client_secret,
        headers=headers,
    )


def _request_options(args: argparse.Namespace) -> dict | None:
    if not args.extra_query:
        return None
    params = dict(pair.split("=", 1) for pair in args.extra_query)
    return {"additional_query_parameters": params}


def cmd_upload(args: argparse.Namespace) -> int:
    client = _client(args)
    with open(args.local_path, "rb") as file:
        metadata = client.objects.upload_object(
            object_path=args.object_path, request=file, request_options=_request_options(args)
        )
    print(f"Uploaded {args.local_path} -> {args.object_path}")
    print(f"  size_bytes:  {metadata.size_bytes}")
    print(f"  expiry_time: {metadata.expiry_time}")
    return 0


def cmd_download(args: argparse.Namespace) -> int:
    client = _client(args)
    os.makedirs(os.path.dirname(os.path.abspath(args.local_path)) or ".", exist_ok=True)
    total_bytes = 0
    with open(args.local_path, "wb") as file:
        for chunk in client.objects.get_object(object_path=args.object_path):
            file.write(chunk)
            total_bytes += len(chunk)
    print(f"Downloaded {args.object_path} -> {args.local_path} ({total_bytes} bytes)")
    return 0


def cmd_metadata(args: argparse.Namespace) -> int:
    client = _client(args)
    metadata = client.objects.get_object_metadata(object_path=args.object_path)
    for key, value in metadata.items():
        print(f"{key}: {value}")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    client = _client(args)
    pager = client.objects.list_objects(prefix=args.prefix or None, max_page_size=args.max_page_size)
    count = 0
    for item in pager:
        path = item.content_identifier.path if item.content_identifier else "<unknown>"
        print(f"{path}\t{item.size_bytes} bytes\tlast_updated={item.last_updated_at}")
        count += 1
    print(f"\n{count} object(s)")
    return 0


def cmd_delete(args: argparse.Namespace) -> int:
    client = _client(args)
    client.objects.delete_object(object_path=args.object_path)
    print(f"Deleted {args.object_path}")
    return 0


def main() -> None:
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
    parser.add_argument(
        "--extra-query", action="append", default=[], metavar="KEY=VALUE",
        help="Extra query parameter to pass through on the operation (repeatable). See this "
        "file's module docstring re: TTL.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    p_upload = subparsers.add_parser("upload", help="Upload a local file as an object")
    p_upload.add_argument("local_path")
    p_upload.add_argument("object_path")
    p_upload.set_defaults(func=cmd_upload)

    p_download = subparsers.add_parser("download", help="Download an object to a local file")
    p_download.add_argument("object_path")
    p_download.add_argument("local_path")
    p_download.set_defaults(func=cmd_download)

    p_metadata = subparsers.add_parser("metadata", help="Show an object's metadata")
    p_metadata.add_argument("object_path")
    p_metadata.set_defaults(func=cmd_metadata)

    p_list = subparsers.add_parser("list", help="List objects, optionally filtered by prefix")
    p_list.add_argument("--prefix", default="")
    p_list.add_argument("--max-page-size", type=int, default=None)
    p_list.set_defaults(func=cmd_list)

    p_delete = subparsers.add_parser("delete", help="Delete an object")
    p_delete.add_argument("object_path")
    p_delete.set_defaults(func=cmd_delete)

    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
