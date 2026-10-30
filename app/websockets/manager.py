
from collections import defaultdict

from fastapi import WebSocket, WebSocketDisconnect


class NotificationConnectionManager:
    """Manage authenticated WebSocket connections by customer ID."""

    def __init__(self):
        self.active_connections: dict[int, set[WebSocket]] = defaultdict(set)

    async def connect(
        self,
        websocket: WebSocket,
        user_id: int,
    ) -> None:
        await websocket.accept()
        self.active_connections[user_id].add(websocket)

    def disconnect(
        self,
        websocket: WebSocket,
        user_id: int,
    ) -> None:
        connections = self.active_connections.get(user_id)

        if connections is None:
            return

        connections.discard(websocket)

        if not connections:
            self.active_connections.pop(user_id, None)

    async def send_to_user(
        self,
        user_id: int,
        message: dict,
    ) -> int:
        """Send a JSON notification to all connections of one user."""

        connections = list(self.active_connections.get(user_id, set()))
        delivered = 0

        for websocket in connections:
            try:
                await websocket.send_json(message)
                delivered += 1
            except Exception:
                self.disconnect(websocket, user_id)

        return delivered

    async def broadcast(
        self,
        user_ids: list[int],
        message: dict,
    ) -> int:
        """Send a notification to a list of individual users."""

        delivered = 0

        for user_id in set(user_ids):
            delivered += await self.send_to_user(user_id, message)

        return delivered


notification_manager = NotificationConnectionManager()
