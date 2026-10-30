
from collections import defaultdict
from datetime import datetime

from fastapi import BackgroundTasks
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.seat import Seat
from app.models.show import Show
from app.models.user import User
from app.models.waitlist import WaitlistEntry
from app.services.notification_service import notify_user_background


async def notify_waitlist_for_released_seats(
    db: AsyncSession,
    show_id: int,
    released_seat_ids: list[int],
    background_tasks: BackgroundTasks | None = None,
) -> dict:
    """
    Notify eligible waitlisted customers in FIFO order.

    Email delivery is queued when BackgroundTasks is provided.
    This service does not reserve seats for waitlisted customers.
    """

    if not released_seat_ids:
        return {"notified_count": 0, "notified_entries": []}

    show_result = await db.execute(
        select(Show).where(Show.id == show_id)
    )
    show = show_result.scalar_one_or_none()

    if show is None or (show.status or "").lower() != "scheduled":
        return {"notified_count": 0, "notified_entries": []}

    seat_result = await db.execute(
        select(Seat).where(
            Seat.id.in_(released_seat_ids),
            Seat.is_active.is_(True),
        )
    )
    released_seats = seat_result.scalars().all()

    available_by_category = defaultdict(int)

    for seat in released_seats:
        available_by_category[
            (seat.category or "").strip().lower()
        ] += 1

    remaining_seats = sum(available_by_category.values())

    if remaining_seats == 0:
        return {"notified_count": 0, "notified_entries": []}

    waitlist_result = await db.execute(
        select(WaitlistEntry, User)
        .join(User, User.id == WaitlistEntry.customer_id)
        .where(
            WaitlistEntry.show_id == show_id,
            WaitlistEntry.status == "Waiting",
        )
        .order_by(
            WaitlistEntry.created_at.asc(),
            WaitlistEntry.id.asc(),
        )
        .with_for_update()
    )

    rows = waitlist_result.all()
    notified_entries = []

    for entry, customer in rows:
        requested = entry.requested_seats

        preferred = (
            entry.preferred_category.strip().lower()
            if entry.preferred_category
            else None
        )

        if preferred:
            if available_by_category[preferred] < requested:
                continue
        elif remaining_seats < requested:
            continue

        message = (
            f"Good news! {requested} seat(s) may now be available "
            f"for show ID {show_id}. "
            "You are receiving this message because you joined its waitlist. "
            "Please open the booking system and check availability. "
            "Seats are not reserved and are available on a first-come, "
            "first-served basis."
        )

        try:
            if background_tasks is not None:
                delivery = await notify_user_background(
                    background_tasks=background_tasks,
                    user_id=customer.id,
                    email=customer.email,
                    subject="Seats available from your waitlist",
                    message=message,
                    notification_type="waitlist_seats_available",
                    data={
                        "show_id": show_id,
                        "waitlist_id": entry.id,
                        "requested_seats": requested,
                        "preferred_category": entry.preferred_category,
                    },
                )
                delivery_scheduled = (
                    delivery.get("email_queued")
                    or delivery.get("websocket_deliveries", 0) > 0
                )
            else:
                # Preserve compatibility with callers outside HTTP requests.
                from app.services.notification_service import notify_user

                delivery = await notify_user(
                    user_id=customer.id,
                    email=customer.email,
                    subject="Seats available from your waitlist",
                    message=message,
                    notification_type="waitlist_seats_available",
                    data={
                        "show_id": show_id,
                        "waitlist_id": entry.id,
                        "requested_seats": requested,
                        "preferred_category": entry.preferred_category,
                    },
                )
                delivery_scheduled = (
                    delivery.get("email_sent")
                    or delivery.get("websocket_deliveries", 0) > 0
                )

        except Exception as exc:
            print(
                f"Waitlist notification failed for entry {entry.id}: "
                f"{type(exc).__name__}: {exc}"
            )
            continue

        if not delivery_scheduled:
            continue

        entry.status = "Notified"
        entry.notified_at = datetime.now()
        notified_entries.append(entry.id)

        if preferred:
            available_by_category[preferred] -= requested
        else:
            seats_to_allocate = requested

            for category in list(available_by_category):
                allocated = min(
                    available_by_category[category],
                    seats_to_allocate,
                )
                available_by_category[category] -= allocated
                seats_to_allocate -= allocated

                if seats_to_allocate == 0:
                    break

        remaining_seats -= requested

    if notified_entries:
        await db.commit()

    return {
        "notified_count": len(notified_entries),
        "notified_entries": notified_entries,
    }
