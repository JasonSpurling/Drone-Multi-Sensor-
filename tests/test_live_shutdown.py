"""app.live.close_all() -- called from app.main's lifespan shutdown so a
graceful stop (SIGTERM) closes every connected dashboard WebSocket
promptly instead of waiting indefinitely for each client to disconnect on
its own (see close_all()'s docstring for why that matters).
"""

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.live import close_all
from app.main import app


def test_close_all_closes_a_connected_websocket(isolated_db):
    with TestClient(app) as client, client.websocket_connect("/ws/live") as ws:
        close_all()
        # The server closed the connection -- the client-side test session
        # surfaces that as a disconnect on the next receive, not a normal
        # message.
        try:
            ws.receive_text()
            raised = False
        except WebSocketDisconnect:
            raised = True
        assert raised


def test_close_all_is_a_no_op_with_no_connected_clients(isolated_db):
    with TestClient(app):
        # Must not raise even though nothing is subscribed.
        close_all()
