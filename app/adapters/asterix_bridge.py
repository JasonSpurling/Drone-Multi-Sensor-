"""Bridges a live ASTERIX CAT048 feed -- e.g. a radar's UDP multicast/
unicast surveillance output -- into this tracker's POST /api/detections
endpoint. Real commercial radars (and most recorded feeds) speak ASTERIX
CAT048 natively; this decodes it directly (see app/adapters/asterix.py)
instead of requiring the radar to be reconfigured to speak something else.

IMPORTANT: an ASTERIX detection reports azimuth_deg/range_m, not lat/lon --
register the radar's mounting position first (see the README's
"Georeferencing" section) or every detection from this adapter is silently
dropped.

Usage:
    pip install -r requirements-radar.txt
    python -m app.adapters.asterix_bridge --listen-port 8600 --sensor-id radar-1
"""

from __future__ import annotations

import argparse
import socket
import urllib.error

from app.adapters.asterix import build_detection_payload
from app.adapters.sdk import add_common_post_args, format_post_error, post_detection


def watch(args: argparse.Namespace) -> None:
    from asterix4py.AsterixParser import AsterixParser

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((args.listen_host, args.listen_port))
    print(
        f"Listening for ASTERIX UDP datagrams on {args.listen_host}:{args.listen_port}, "
        f"posting to {args.api_url} as '{args.sensor_id}' ..."
    )
    while True:
        datagram, _addr = sock.recvfrom(65535)
        try:
            records = AsterixParser(datagram).get_result()
        except Exception as exc:  # noqa: BLE001 -- deliberately broad, see comment below
            # A single malformed/garbled datagram (a real risk over UDP --
            # no retransmission, no framing guarantee) must not take the
            # listener down; log and keep going, same posture as a bad
            # line on the SBS-1 bridge. A third-party parser's exact
            # exception type for "malformed input" isn't part of its
            # documented API, so narrowing this risks silently missing a
            # real parse-failure mode instead of logging it.
            print(f"ERROR decoding ASTERIX datagram: {exc}")
            continue

        for record in records.values():
            if record.get("cat") != 48:
                continue
            payload = build_detection_payload(record, args.sensor_id, args.confidence)
            if payload is None:
                continue
            try:
                post_detection(
                    args.api_url, payload, args.api_key,
                    max_retries=args.max_retries, retry_backoff_s=args.retry_backoff,
                )
                print(f"-> az={payload['azimuth_deg']:.1f} range={payload['range_m']:.0f}m")
            except urllib.error.URLError as exc:
                print(f"ERROR posting detection: {format_post_error(exc)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--listen-host", default="0.0.0.0")
    parser.add_argument("--listen-port", type=int, default=8600)
    add_common_post_args(parser, default_sensor_id="asterix-radar-1")
    parser.add_argument("--confidence", type=float, default=0.9)
    args = parser.parse_args()
    watch(args)


if __name__ == "__main__":  # pragma: no cover -- only executes when this
    # file is run directly as a script (python -m / a shebang), never when
    # imported under pytest, so it is structurally unreachable in-process;
    # main()'s own body is covered by tests that call it directly.
    main()
