"""Motion-cueing adapter for a camera/RTSP stream: runs OpenCV background
subtraction to find moving regions in frame and posts them as `camera`
detections.

IMPORTANT LIMITATION: this is a motion detector, not an object classifier
-- it cannot tell a drone from a bird, a cat, or a swaying tree branch. It
reports a single fixed, operator-supplied confidence for every motion
event (not derived from what's actually in frame), so the resulting
classification (drone vs bird vs unknown, see app/classification.py) is
only as good as that manual estimate for your specific camera's field of
view. A serious deployment should replace this with a trained object
detector (YOLO or similar) scoring actual object class, not just motion.

Since a single monocular camera can't measure range, every detection is
reported at a fixed lat/lon representing the center of the camera's field
of view (--target-lat/--target-lon) rather than a triangulated position --
good enough to flag "something is moving in this camera's view" and let
classification fusion (app/fusion.py) weigh it against more precise
sensors, not to place the track accurately on its own.

Usage:
    python -m app.adapters.camera_motion --source 0 --target-lat 51.5 --target-lon -0.1
    python -m app.adapters.camera_motion --source rtsp://192.168.1.50/stream1 \\
        --target-lat 51.5 --target-lon -0.1 --confidence 0.6
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

DEFAULT_MIN_CONTOUR_AREA_PX = 500.0


def build_detection_payload(
    sensor_id: str, target_lat: float, target_lon: float, confidence: float, motion_area_px: float
) -> dict:
    return {
        "sensor_id": sensor_id,
        "sensor_type": "camera",
        "timestamp": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
        "latitude": target_lat,
        "longitude": target_lon,
        "confidence": confidence,
        "raw_data": {"motion_area_px": motion_area_px},
    }


def should_post(motion_area_px: float, min_area_px: float, elapsed_s: float, min_interval_s: float) -> bool:
    return motion_area_px >= min_area_px and elapsed_s >= min_interval_s


def post_detection(url: str, payload: dict, api_key: str = "") -> dict:
    data = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read())


def _largest_contour_area(frame, subtractor) -> float:
    import cv2  # imported lazily so parsing/testing this module doesn't require OpenCV installed

    mask = subtractor.apply(frame)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    return max((cv2.contourArea(c) for c in contours), default=0.0)


def watch(args: argparse.Namespace) -> None:
    import cv2

    source = int(args.source) if args.source.isdigit() else args.source
    capture = cv2.VideoCapture(source)
    if not capture.isOpened():
        raise SystemExit(f"Could not open video source: {args.source}")

    subtractor = cv2.createBackgroundSubtractorMOG2(detectShadows=False)
    last_post = 0.0

    print(f"Watching {args.source} for motion, posting to {args.api_url} as '{args.sensor_id}' ...")
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                print("Stream ended or unreadable frame; stopping.")
                return

            motion_area = _largest_contour_area(frame, subtractor)
            now = time.monotonic()
            if should_post(motion_area, args.min_area, now - last_post, args.min_interval):
                payload = build_detection_payload(
                    args.sensor_id, args.target_lat, args.target_lon, args.confidence, motion_area
                )
                try:
                    result = post_detection(args.api_url, payload, args.api_key)
                    print(f"-> motion (area={motion_area:.0f}px) track {result['track_id']}")
                except urllib.error.URLError as exc:
                    print(f"ERROR posting detection: {exc}")
                last_post = now
    finally:
        capture.release()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", default="0", help="cv2.VideoCapture source: camera index or RTSP/file URL")
    parser.add_argument("--api-url", default="http://127.0.0.1:8000/api/detections")
    parser.add_argument("--sensor-id", default="camera-1")
    parser.add_argument("--target-lat", type=float, required=True, help="Latitude of the camera's field of view")
    parser.add_argument("--target-lon", type=float, required=True, help="Longitude of the camera's field of view")
    parser.add_argument(
        "--confidence", type=float, default=0.5,
        help="Fixed confidence reported for every motion event -- this adapter can't classify what it "
             "sees, so tune this to your scene (e.g. a camera pointed only at open sky can reasonably "
             "use a higher value than one that also sees birds/traffic/trees)",
    )
    parser.add_argument("--min-area", type=float, default=DEFAULT_MIN_CONTOUR_AREA_PX, help="Min motion area in pixels to report")
    parser.add_argument("--min-interval", type=float, default=2.0, help="Seconds between posted detections")
    parser.add_argument("--api-key", default=os.getenv("DRONE_API_KEY", ""))
    args = parser.parse_args()
    watch(args)


if __name__ == "__main__":
    main()
