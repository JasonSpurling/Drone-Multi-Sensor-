"""GET /api/sensors/{sensor_id}/live -- a real, live MJPEG re-proxy of a
registered camera's RTSP/HTTP stream, so the dashboard can show what a
camera-type sensor is currently seeing without the browser ever
connecting to the camera (or holding its login credentials) directly.

Deliberately NOT full frame-rate broadcast video: _MAX_FPS throttles this
to a periodically-refreshing live view, the same "confirm what the
camera currently sees" job a security-camera dashboard's live tile does,
not a claim of smooth real-time video this app has no use for and no
bandwidth budget to actually deliver to several simultaneously-open
dashboard tabs. Still a genuinely live feed from the real camera, not a
static thumbnail or a mockup.
"""

from __future__ import annotations

import time
from collections.abc import Iterator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from app.auth import ROLE_ADMIN, ROLE_OPERATOR, ROLE_VIEWER, Principal, require_role_allow_query_key
from app.db import get_sensor_registration

router = APIRouter()

_BOUNDARY = "frame"
_JPEG_QUALITY = 70
_MAX_FPS = 8


def _mjpeg_frames(stream_url: str) -> Iterator[bytes]:
    """Blocking, synchronous generator -- deliberately not async, since
    OpenCV's VideoCapture has no async API of its own. Starlette's
    StreamingResponse already runs a plain (non-async) iterator in a
    thread pool automatically (see starlette.responses.StreamingResponse
    .__init__'s use of iterate_in_threadpool), so this never blocks the
    event loop despite that.
    """
    import cv2  # imported lazily, same reasoning as app/adapters/camera_motion.py's
    # identical pattern: opencv is an optional extra (requirements-camera.txt), not a
    # core dependency of the API server, so importing it at module load time would make
    # every request to this app fail if the extras aren't installed, not just this one.

    capture = cv2.VideoCapture(stream_url)
    if not capture.isOpened():
        capture.release()
        raise RuntimeError("Could not open camera stream")
    try:
        min_interval_s = 1.0 / _MAX_FPS
        while True:
            start = time.monotonic()
            ok, frame = capture.read()
            if not ok:
                break
            ok, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, _JPEG_QUALITY])
            if not ok:
                continue
            payload = buffer.tobytes()
            yield (
                b"--" + _BOUNDARY.encode() + b"\r\n"
                b"Content-Type: image/jpeg\r\n"
                b"Content-Length: " + str(len(payload)).encode() + b"\r\n\r\n"
                + payload + b"\r\n"
            )
            elapsed = time.monotonic() - start
            if elapsed < min_interval_s:
                time.sleep(min_interval_s - elapsed)
    finally:
        capture.release()


def _frames_or_end(stream_url: str) -> Iterator[bytes]:
    """A camera that's unreachable ends the stream (the browser's <img>
    tag just stops updating) rather than crashing the response mid-flight
    -- by the time _mjpeg_frames' isOpened() check can fail, headers are
    already sent, so raising HTTPException here wouldn't do anything an
    honest closed connection doesn't already do.
    """
    try:
        yield from _mjpeg_frames(stream_url)
    except RuntimeError:
        return


@router.get("/sensors/{sensor_id}/live")
def get_camera_live_view(
    sensor_id: str,
    principal: Principal = Depends(require_role_allow_query_key(ROLE_VIEWER, ROLE_OPERATOR, ROLE_ADMIN)),
) -> StreamingResponse:
    registration = get_sensor_registration(sensor_id, principal.site_id)
    if registration is None:
        raise HTTPException(status_code=404, detail="Sensor not registered")
    stream_url = registration.get("camera_stream_url")
    if not stream_url:
        raise HTTPException(status_code=409, detail="No live view configured for this sensor")

    try:
        import cv2  # noqa: F401  -- checked here so a missing extra fails fast with a
        # clear, actionable error, rather than as a silently-empty stream once the
        # thread-pooled generator below actually tries the same import.
    except ImportError as exc:
        raise HTTPException(
            status_code=501,
            detail="Live view requires the camera extras: pip install -r requirements-camera.txt",
        ) from exc

    return StreamingResponse(
        _frames_or_end(stream_url), media_type=f"multipart/x-mixed-replace; boundary={_BOUNDARY}"
    )
