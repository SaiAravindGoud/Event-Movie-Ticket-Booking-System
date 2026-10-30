
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from app.auth.security import decode_access_token
from app.database import AsyncSessionLocal
from app.models.user import User
from app.websockets.manager import notification_manager

router = APIRouter(tags=["Notifications"])


@router.websocket("/ws/notifications")
async def notification_websocket(websocket: WebSocket):
    """Connect an active user to their private notification channel."""

    token = websocket.query_params.get("token")

    if not token:
        await websocket.close(code=1008, reason="Authentication required.")
        return

    payload = decode_access_token(token)

    if not payload:
        await websocket.close(code=1008, reason="Invalid or expired token.")
        return

    subject = payload.get("sub")

    try:
        user_id = int(subject)
    except (TypeError, ValueError):
        await websocket.close(code=1008, reason="Invalid token subject.")
        return

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(User).where(
                User.id == user_id,
                User.is_active.is_(True),
            )
        )
        user = result.scalar_one_or_none()

    if user is None:
        await websocket.close(code=1008, reason="User is unavailable.")
        return

    await notification_manager.connect(websocket, user_id)

    try:
        await websocket.send_json(
            {
                "type": "connection_established",
                "message": "Notification connection established.",
                "user_id": user_id,
            }
        )

        while True:
            # Keep the connection alive and accept client messages.
            # Actual notifications are sent through notification_manager.
            await websocket.receive_text()

    except WebSocketDisconnect:
        pass
    finally:
        notification_manager.disconnect(websocket, user_id)
