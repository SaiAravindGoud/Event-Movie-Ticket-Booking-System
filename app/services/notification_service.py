
from fastapi import BackgroundTasks

from app.services.email_service import send_email
from app.websockets.manager import notification_manager


async def _send_notification_email(
    user_id: int,
    email: str,
    subject: str,
    message: str,
) -> bool:
    """Send an email safely as a background task."""
    try:
        return await send_email(
            recipient=email,
            subject=subject,
            body=message,
        )
    except Exception as exc:
        print(
            f"Notification email failed for user {user_id}: "
            f"{type(exc).__name__}: {exc}"
        )
        return False


async def notify_user(
    user_id: int,
    email: str | None,
    subject: str,
    message: str,
    notification_type: str,
    data: dict | None = None,
) -> dict:
    """Send email and WebSocket notifications for scheduled jobs."""

    email_sent = False

    if email:
        email_sent = await _send_notification_email(
            user_id,
            email,
            subject,
            message,
        )

    websocket_deliveries = 0

    payload = {
        "type": notification_type,
        "title": subject,
        "message": message,
        "data": data or {},
    }

    try:
        websocket_deliveries = await notification_manager.send_to_user(
            user_id=user_id,
            message=payload,
        )
    except Exception as exc:
        print(
            f"WebSocket notification failed for user {user_id}: "
            f"{type(exc).__name__}: {exc}"
        )

    return {
        "email_sent": email_sent,
        "websocket_deliveries": websocket_deliveries,
    }


async def notify_user_background(
    background_tasks: BackgroundTasks,
    user_id: int,
    email: str | None,
    subject: str,
    message: str,
    notification_type: str,
    data: dict | None = None,
) -> dict:
    """Queue email delivery and send the WebSocket notification immediately."""

    email_queued = False

    if email:
        background_tasks.add_task(
            _send_notification_email,
            user_id,
            email,
            subject,
            message,
        )
        email_queued = True

    websocket_deliveries = 0

    payload = {
        "type": notification_type,
        "title": subject,
        "message": message,
        "data": data or {},
    }

    try:
        websocket_deliveries = await notification_manager.send_to_user(
            user_id=user_id,
            message=payload,
        )
    except Exception as exc:
        print(
            f"WebSocket notification failed for user {user_id}: "
            f"{type(exc).__name__}: {exc}"
        )

    return {
        "email_queued": email_queued,
        "websocket_deliveries": websocket_deliveries,
    }
