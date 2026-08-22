"""GET /ws/live -- pushes track/incident updates to a connected dashboard
in near-real-time (see app/live.py for the underlying pub/sub), instead
of the client waiting up to POLL_MS for its next poll. Not a replacement
for polling: dashboard.html keeps its (now longer-interval) polling loop
as a fallback -- see that file's comment on why a WebSocket alone isn't
enough for multi-replica deployments.
"""

from __future__ import annotations

import contextlib
import logging

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from app.auth import ROLE_ADMIN, ROLE_OPERATOR, ROLE_VIEWER, authenticate_key
from app.live import subscribe, unsubscribe

logger = logging.getLogger(__name__)
router = APIRouter()

_REQUIRED_ROLES = {ROLE_VIEWER, ROLE_OPERATOR, ROLE_ADMIN}


@router.websocket("/ws/live")
async def live_updates(websocket: WebSocket, api_key: str | None = None) -> None:
    # A browser WebSocket client can't set a custom X-API-Key header --
    # authenticates via a query param instead (see authenticate_key()'s
    # docstring in app/auth.py). Same checks (revocation, expiry, role,
    # site scoping) as every other endpoint, just a different transport
    # for the credential.
    try:
        principal = authenticate_key(
            api_key, _REQUIRED_ROLES, missing_key_detail="Missing api_key query parameter"
        )
    except HTTPException as exc:
        # WebSocket close codes 4401/4403 (the 4000-4999 range is reserved
        # for application use) mirror the 401/403 an equivalent HTTP
        # request would get, so a client can tell "bad credentials" apart
        # from any other disconnect reason.
        await websocket.close(code=4401 if exc.status_code == 401 else 4403, reason=str(exc.detail))
        return

    await websocket.accept()
    queue = subscribe(principal.site_id)
    try:
        while True:
            payload = await queue.get()
            if payload is None:
                # app.live.close_all()'s shutdown sentinel -- not a real
                # event, just this connection's turn to end.
                break
            await websocket.send_text(payload)
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("Live-update WebSocket connection failed")
    finally:
        unsubscribe(principal.site_id, queue)
        with contextlib.suppress(RuntimeError):
            # RuntimeError if the client already closed it -- either way,
            # this connection is done.
            await websocket.close()
