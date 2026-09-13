"""Object-detection camera cueing using a YOLO model (via the
`ultralytics` package), instead of plain motion detection
(app/adapters/camera_motion.py). Distinguishes actual object classes --
bird, person, car, etc. -- so a passing car or pedestrian doesn't get
posted as an ambiguous aerial contact the way any motion blob does, and
a confidently-identified bird is reported as low-confidence (correctly
resolving to BIRD, not DRONE, through the existing classification rules)
instead of being lumped in with everything else that moves.

IMPORTANT LIMITATION: there is no "drone" class in stock COCO-pretrained
YOLO weights -- the 80 COCO classes cover common objects (person, car,
airplane, bird, kite, ...) but not drones, because no such public,
generically-licensed dataset/label exists in the standard release. This
adapter can rule OUT confidently-non-aerial classes and treat a 'bird'
match as real evidence, but anything else detected in frame (a kite, an
airplane, or any other object with no more specific class) is reported as
an unidentified object at moderate confidence, not a confirmed drone. A
deployment wanting real drone/not-drone classification from vision needs
a model fine-tuned on a labeled drone dataset (several exist publicly,
e.g. on Roboflow Universe) -- point --model at those weights once you
have them; this adapter works with any YOLO-format model, not just the
stock COCO one.

Usage:
    pip install -r requirements-camera.txt
    python -m app.adapters.camera_yolo --source 0 --target-lat 51.5 --target-lon -0.1
    python -m app.adapters.camera_yolo --source rtsp://192.168.1.50/stream1 \\
        --target-lat 51.5 --target-lon -0.1 --model my_drone_model.pt
"""

from __future__ import annotations

import argparse
import os
import time
import urllib.error
from datetime import UTC, datetime

from app.adapters.sdk import add_common_post_args, format_post_error, post_detection

# Classes confidently NOT an aerial object of interest -- skipped outright,
# unlike plain motion detection which can't tell a car from a drone.
_NON_AERIAL_CLASSES = {
    "person", "bicycle", "car", "motorcycle", "bus", "truck", "boat", "train",
    "dog", "cat", "horse", "sheep", "cow",
}

# A class the model is confident is a bird gets a *low* reported
# confidence -- correctly resolving to BIRD, not DRONE, through the
# existing camera classification thresholds (app/classification.py:
# below DRONE_BIRD_CONFIDENCE_THRESHOLD is BIRD).
_BIRD_CLASSES = {"bird"}

# For anything else detected (kite, airplane, or an unrecognized class),
# report the model's own detection confidence, scaled down slightly since
# "definitely some object" isn't the same as "definitely a drone" --
# letting the existing threshold/fusion logic weigh it appropriately.
_UNIDENTIFIED_CONFIDENCE_SCALE = 0.85


def classify_yolo_detection(class_name: str, model_confidence: float) -> float | None:
    """Returns the confidence value to report for this YOLO detection (fed
    into the existing camera classification thresholds), or None if this
    class should be skipped entirely (confidently not an aerial object).
    """
    if class_name in _NON_AERIAL_CLASSES:
        return None
    if class_name in _BIRD_CLASSES:
        return min(0.2, model_confidence * 0.3)
    return min(0.95, model_confidence * _UNIDENTIFIED_CONFIDENCE_SCALE)


def build_detection_payload(
    sensor_id: str, target_lat: float, target_lon: float, class_name: str, model_confidence: float,
    box_xyxy: tuple[float, float, float, float], snapshot_path: str | None = None,
) -> dict | None:
    confidence = classify_yolo_detection(class_name, model_confidence)
    if confidence is None:
        return None
    raw_data = {
        "yolo_class": class_name,
        "yolo_confidence": model_confidence,
        "box_xyxy": list(box_xyxy),
    }
    if snapshot_path is not None:
        # Consumed by app/adapters/lattice_bridge.py's optional thumbnail
        # attachment (Lattice Objects API) -- see --snapshot-dir below.
        # Not read by anything in the core ingest path.
        raw_data["snapshot_path"] = snapshot_path
    return {
        "sensor_id": sensor_id,
        "sensor_type": "camera",
        "timestamp": datetime.now(UTC).replace(tzinfo=None).isoformat(),
        "latitude": target_lat,
        "longitude": target_lon,
        "confidence": confidence,
        "raw_data": raw_data,
    }


def watch(args: argparse.Namespace) -> None:
    import cv2
    from ultralytics import YOLO

    model = YOLO(args.model)
    source = int(args.source) if args.source.isdigit() else args.source
    capture = cv2.VideoCapture(source)
    if not capture.isOpened():
        raise SystemExit(f"Could not open video source: {args.source}")

    last_post = 0.0
    print(f"Watching {args.source} with {args.model}, posting to {args.api_url} as '{args.sensor_id}' ...")
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                print("Stream ended or unreadable frame; stopping.")
                return

            now = time.monotonic()
            if now - last_post < args.min_interval:
                continue

            results = model.predict(frame, conf=args.min_model_confidence, verbose=False)
            for result in results:
                names = result.names
                for box in result.boxes:
                    class_name = names[int(box.cls[0])]
                    model_confidence = float(box.conf[0])
                    x1, y1, x2, y2 = (float(v) for v in box.xyxy[0])
                    xyxy = (x1, y1, x2, y2)
                    snapshot_path = None
                    if args.snapshot_dir:
                        snapshot_path = os.path.join(
                            args.snapshot_dir, f"{args.sensor_id}-{int(now * 1000)}.jpg"
                        )
                        cv2.imwrite(snapshot_path, frame)
                    payload = build_detection_payload(
                        args.sensor_id, args.target_lat, args.target_lon, class_name, model_confidence, xyxy,
                        snapshot_path=snapshot_path,
                    )
                    if payload is None:
                        continue
                    try:
                        result_json = post_detection(
                            args.api_url, payload, args.api_key,
                            max_retries=args.max_retries, retry_backoff_s=args.retry_backoff,
                        )
                        print(f"-> {class_name} (conf={model_confidence:.2f}) track {result_json['track_id']}")
                    except urllib.error.URLError as exc:
                        print(f"ERROR posting detection: {format_post_error(exc)}")
                    last_post = now
    finally:
        capture.release()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", default="0", help="cv2.VideoCapture source: camera index or RTSP/file URL")
    parser.add_argument("--model", default="yolov8n.pt", help="YOLO model path/name (ultralytics format)")
    add_common_post_args(parser, default_sensor_id="camera-yolo-1")
    parser.add_argument("--target-lat", type=float, required=True)
    parser.add_argument("--target-lon", type=float, required=True)
    parser.add_argument("--min-model-confidence", type=float, default=0.4)
    parser.add_argument("--min-interval", type=float, default=1.0, help="Seconds between processed frames")
    parser.add_argument(
        "--snapshot-dir", default=None,
        help="If set, save a JPEG of each posted detection's frame here and record its path in "
        "raw_data.snapshot_path -- picked up by app.adapters.lattice_bridge's optional thumbnail "
        "attachment (Lattice Objects API). Off by default: no extra disk I/O or storage growth "
        "unless you're actually running the Lattice bridge.",
    )
    args = parser.parse_args()
    watch(args)


if __name__ == "__main__":  # pragma: no cover -- only executes when this
    # file is run directly as a script (python -m / a shebang), never when
    # imported under pytest, so it is structurally unreachable in-process;
    # main()'s own body is covered by tests that call it directly.
    main()
