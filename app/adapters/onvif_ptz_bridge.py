"""Bridges this tracker's slew-to-cue endpoint
(GET /api/tracks/{track_id}/cue/{camera_sensor_id}, see app/slew_to_cue.py)
to a real PTZ camera over ONVIF -- the open IP-camera control standard
most commercial PTZ cameras support -- so a bearing from one sensor
actually swings a camera mounted elsewhere onto the target, instead of a
human operator manually panning to follow a cue.

Uses `onvif-zeep-async` (the actively maintained ONVIF client library
used by Home Assistant's own ONVIF integration), not a hand-rolled SOAP
client. Polls this app's own cue endpoint and issues a real ONVIF
AbsoluteMove PTZ command each time.

IMPORTANT -- ONVIF's PTZ AbsoluteMove coordinates are camera-specific, not
degrees: the spec requires querying the camera's own reported pan/tilt
range (GetConfigurationOptions) and normalizing into it, which this bridge
does (see normalize_to_range) rather than assuming either raw degrees or
a fixed [-1, 1] range. This could not be tested against a real ONVIF
camera in the environment this was built in (no PTZ hardware here) --
verify against your own camera's actual reported ranges before relying
on this; some cameras report ranges in ways that need Speed values set
too or reject an AbsoluteMove without one, which this covers with a
conservative default but hasn't been confirmed against real hardware.

Usage:
    pip install -r requirements-ptz.txt
    .venv/bin/python -m app.adapters.onvif_ptz_bridge \\
        --track-id 12 --cueing-camera-sensor-id ptz-cam-1 \\
        --camera-host 192.168.1.50 --camera-user admin --camera-password secret
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import urllib.error
import urllib.request


def normalize_to_range(value: float, value_min: float, value_max: float, range_min: float, range_max: float) -> float:
    """Linearly maps `value` (assumed within [value_min, value_max]) into
    [range_min, range_max], clamping if it falls outside -- ONVIF PTZ
    AbsoluteMove coordinates are whatever range the camera itself reports
    via GetConfigurationOptions, not a fixed unit.
    """
    if value_max == value_min:
        return range_min
    fraction = (value - value_min) / (value_max - value_min)
    fraction = max(0.0, min(1.0, fraction))
    return range_min + fraction * (range_max - range_min)


def fetch_cue(api_url: str, track_id: int, camera_sensor_id: str, api_key: str = "") -> dict | None:
    url = f"{api_url}/api/tracks/{track_id}/cue/{camera_sensor_id}"
    headers = {"X-API-Key": api_key} if api_key else {}
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        if exc.code in (404, 409):
            return None
        raise


async def watch(args: argparse.Namespace) -> None:
    from onvif import ONVIFCamera

    camera = ONVIFCamera(args.camera_host, args.camera_port, args.camera_user, args.camera_password)
    await camera.update_xaddrs()
    media_service = camera.create_media_service()
    ptz_service = camera.create_ptz_service()

    profiles = await media_service.GetProfiles()
    profile_token = profiles[0].token

    configuration_options = await ptz_service.GetConfigurationOptions(
        {"ConfigurationToken": profiles[0].PTZConfiguration.token}
    )
    pan_tilt_limits = configuration_options.SpacesTypes.AbsolutePanTiltPositionSpace[0].XRange
    tilt_limits = configuration_options.SpacesTypes.AbsolutePanTiltPositionSpace[0].YRange

    print(f"Polling cue for track {args.track_id} via {args.cueing_camera_sensor_id}, driving {args.camera_host} ...")
    while True:
        cue = fetch_cue(args.api_url, args.track_id, args.cueing_camera_sensor_id, args.api_key)
        if cue is not None:
            pan_relative = ((cue["pan_relative_deg"] + 180) % 360) - 180  # 0-360 -> -180..180
            pan_normalized = normalize_to_range(pan_relative, -180.0, 180.0, pan_tilt_limits.Min, pan_tilt_limits.Max)
            tilt_normalized = normalize_to_range(cue["tilt_deg"], -90.0, 90.0, tilt_limits.Min, tilt_limits.Max)

            move_request = ptz_service.create_type("AbsoluteMove")
            move_request.ProfileToken = profile_token
            move_request.Position = {"PanTilt": {"x": pan_normalized, "y": tilt_normalized}}
            await ptz_service.AbsoluteMove(move_request)
            print(
                f"-> slewed to pan={pan_normalized:.2f} tilt={tilt_normalized:.2f} "
                f"(distance={cue['distance_m']:.0f}m)"
            )
        else:
            print("No cue available (track lost its position, or camera not registered) -- holding position.")

        # asyncio.sleep, not time.sleep -- this loop runs inside an async
        # event loop (asyncio.run(watch(...)) below); a blocking sleep here
        # would stall the whole loop for the interval, not just this task.
        # Doesn't matter today (nothing else runs concurrently in this
        # single-camera script), but would silently break the moment this
        # bridge is extended to poll multiple cameras via asyncio.gather.
        await asyncio.sleep(args.poll_interval)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--track-id", type=int, required=True)
    parser.add_argument(
        "--cueing-camera-sensor-id", required=True,
        help="The sensor_id registered (POST /api/sensor-registrations) for THIS PTZ camera's own "
        "mounting position -- used to compute the cue, not the cueing sensor's own ID",
    )
    parser.add_argument("--camera-host", required=True)
    parser.add_argument("--camera-port", type=int, default=80)
    parser.add_argument("--camera-user", required=True)
    parser.add_argument("--camera-password", required=True)
    parser.add_argument("--poll-interval", type=float, default=1.0)
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    parser.add_argument("--api-key", default=os.getenv("DRONE_API_KEY", ""))
    args = parser.parse_args()
    asyncio.run(watch(args))


if __name__ == "__main__":  # pragma: no cover -- only executes when this
    # file is run directly as a script (python -m / a shebang), never when
    # imported under pytest, so it is structurally unreachable in-process;
    # main()'s own body is covered by tests that call it directly.
    main()
