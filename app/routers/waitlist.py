
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_roles
from app.database import get_db
from app.models.booking import Booking
from app.models.event import Event
from app.models.seat import Seat
from app.models.show import Show
from app.models.waitlist import WaitlistEntry
from app.models.user import User
from app.redis_client import get_redis
from app.services.seat_lock_service import (
    get_seat_map,
    get_show_or_404,
)
from app.services.notification_service import notify_user

router = APIRouter(tags=["Waitlist"])


class WaitlistCreateRequest(BaseModel):
    requested_seats: int = Field(default=1, ge=1, le=10)
    preferred_category: str | None = Field(default=None, max_length=50)


def _current_time_for_show(show_start: datetime) -> datetime:
    if show_start.tzinfo is None:
        return datetime.now()
    return datetime.now(show_start.tzinfo)


async def _get_event_for_show(
    db: AsyncSession,
    show: Show,
) -> Event:
    result = await db.execute(
        select(Event).where(Event.id == show.event_id)
    )
    event = result.scalar_one_or_none()

    if event is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Event not found.",
        )

    return event


def _ensure_event_manager(
    current_user: User,
    event: Event,
):
    is_admin = current_user.role == "Admin"

    is_approved_organizer = (
        current_user.role == "Organizer"
        and event.organizer_id == current_user.id
        and current_user.organizer_approved
    )

    if not is_admin and not is_approved_organizer:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only an admin or the approved event organizer can perform this action.",
        )


@router.post(
    "/shows/{show_id}/waitlist",
    status_code=status.HTTP_201_CREATED,
)
async def join_waitlist(
    show_id: int,
    payload: WaitlistCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("Customer")),
):
    show = await get_show_or_404(db, show_id)

    if show.status.lower() != "scheduled":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only scheduled shows can have a waitlist.",
        )

    if show.start_time <= _current_time_for_show(show.start_time):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This show has already started.",
        )

    existing_entry = await db.execute(
        select(WaitlistEntry).where(
            WaitlistEntry.show_id == show_id,
            WaitlistEntry.customer_id == current_user.id,
        )
    )

    if existing_entry.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You are already on the waitlist for this show.",
        )

    existing_booking = await db.execute(
        select(Booking.id).where(
            Booking.show_id == show_id,
            Booking.customer_id == current_user.id,
            func.lower(Booking.status).in_(
                ["pending", "confirmed", "paid", "completed", "booked"]
            ),
        ).limit(1)
    )

    if existing_booking.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You already have a booking for this show.",
        )

    if payload.preferred_category:
        category_result = await db.execute(
            select(Seat.category)
            .join(Show, Show.screen_id == Seat.screen_id)
            .where(
                Show.id == show_id,
                Seat.is_active.is_(True),
                func.lower(Seat.category)
                == payload.preferred_category.strip().lower(),
            )
            .limit(1)
        )

        actual_category = category_result.scalar_one_or_none()

        if actual_category is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Preferred category is not available on this screen.",
            )

    redis = await get_redis()
    seats = await get_seat_map(db, show, redis)

    available_seats = sum(
        1 for seat in seats if seat["available"]
    )

    if available_seats > 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "This show is not sold out. "
                "Please book from the available seats instead."
            ),
        )

    entry = WaitlistEntry(
        show_id=show_id,
        customer_id=current_user.id,
        requested_seats=payload.requested_seats,
        preferred_category=(
            payload.preferred_category.strip()
            if payload.preferred_category
            else None
        ),
        status="Waiting",
    )

    db.add(entry)

    try:
        await db.commit()
        await db.refresh(entry)
    except Exception:
        await db.rollback()
        raise

    return {
        "message": "Added to the waitlist.",
        "id": entry.id,
        "show_id": entry.show_id,
        "customer_id": entry.customer_id,
        "requested_seats": entry.requested_seats,
        "preferred_category": entry.preferred_category,
        "status": entry.status,
        "created_at": entry.created_at,
    }


@router.get("/shows/{show_id}/waitlist")
async def list_waitlist(
    show_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    show_result = await db.execute(
        select(Show).where(Show.id == show_id)
    )
    show = show_result.scalar_one_or_none()

    if show is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Show not found.",
        )

    event = await _get_event_for_show(db, show)

    is_admin = current_user.role == "Admin"
    is_organizer = (
        current_user.role == "Organizer"
        and event.organizer_id == current_user.id
        and current_user.organizer_approved
    )

    if not is_admin and not is_organizer:
        if current_user.role != "Customer":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to view this waitlist.",
            )

        result = await db.execute(
            select(WaitlistEntry)
            .where(
                WaitlistEntry.show_id == show_id,
                WaitlistEntry.customer_id == current_user.id,
            )
            .order_by(
                WaitlistEntry.created_at.asc(),
                WaitlistEntry.id.asc(),
            )
        )
    else:
        result = await db.execute(
            select(WaitlistEntry)
            .where(WaitlistEntry.show_id == show_id)
            .order_by(
                WaitlistEntry.created_at.asc(),
                WaitlistEntry.id.asc(),
            )
        )

    entries = list(result.scalars().all())

    return {
        "show_id": show.id,
        "count": len(entries),
        "waitlist": [
            {
                "id": entry.id,
                "customer_id": entry.customer_id,
                "requested_seats": entry.requested_seats,
                "preferred_category": entry.preferred_category,
                "status": entry.status,
                "created_at": entry.created_at,
                "notified_at": entry.notified_at,
            }
            for entry in entries
        ],
    }


@router.post("/waitlist/{waitlist_id}/notify")
async def notify_waitlisted_customer(
    waitlist_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    entry_result = await db.execute(
        select(WaitlistEntry).where(
            WaitlistEntry.id == waitlist_id
        )
    )
    entry = entry_result.scalar_one_or_none()

    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Waitlist entry not found.",
        )

    show = await get_show_or_404(db, entry.show_id)
    event = await _get_event_for_show(db, show)
    _ensure_event_manager(current_user, event)

    if entry.status != "Waiting":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This waitlist entry is not waiting for notification.",
        )

    if show.status.lower() != "scheduled":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Notifications can only be sent for scheduled shows.",
        )

    if show.start_time <= _current_time_for_show(show.start_time):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This show has already started.",
        )

    first_waiting_result = await db.execute(
        select(WaitlistEntry.id)
        .where(
            WaitlistEntry.show_id == entry.show_id,
            WaitlistEntry.status == "Waiting",
        )
        .order_by(
            WaitlistEntry.created_at.asc(),
            WaitlistEntry.id.asc(),
        )
        .limit(1)
    )
    first_waiting_id = first_waiting_result.scalar_one_or_none()

    if first_waiting_id != entry.id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An earlier waitlist entry must be processed first.",
        )

    redis = await get_redis()
    seat_map = await get_seat_map(db, show, redis)

    available_seats = [
        seat for seat in seat_map
        if seat.get("available") is True
    ]

    preferred = (
        entry.preferred_category.strip().lower()
        if entry.preferred_category
        else None
    )

    if preferred:
        matching_seats = [
            seat for seat in available_seats
            if (seat.get("category") or "").strip().lower() == preferred
        ]
    else:
        matching_seats = available_seats

    if len(matching_seats) < entry.requested_seats:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="There are not enough currently available seats for this waitlist entry.",
        )

    customer_result = await db.execute(
        select(User).where(User.id == entry.customer_id)
    )
    customer = customer_result.scalar_one_or_none()

    if customer is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Waitlisted customer not found.",
        )

    message = (
        f"Good news! {entry.requested_seats} seat(s) may now be available "
        f"for show ID {show.id}. "
        "You are receiving this message because you joined its waitlist. "
        "Please open the booking system and check availability. "
        "Seats are not reserved and remain available on a first-come, "
        "first-served basis."
    )

    delivery = await notify_user(
        user_id=customer.id,
        email=customer.email,
        subject="Seats available from your waitlist",
        message=message,
        notification_type="waitlist_seats_available",
        data={
            "show_id": show.id,
            "waitlist_id": entry.id,
            "requested_seats": entry.requested_seats,
            "preferred_category": entry.preferred_category,
        },
    )

    email_sent = delivery.get("email_sent", False)
    websocket_deliveries = delivery.get("websocket_deliveries", 0)

    if not email_sent and websocket_deliveries <= 0:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Notification could not be delivered. The entry remains on the waitlist.",
        )

    entry.status = "Notified"
    entry.notified_at = datetime.now()

    await db.commit()
    await db.refresh(entry)

    return {
        "message": "Waitlisted customer notified successfully.",
        "waitlist_id": entry.id,
        "status": entry.status,
        "email_sent": email_sent,
        "websocket_deliveries": websocket_deliveries,
        "notified_at": entry.notified_at,
    }


@router.delete("/waitlist/{waitlist_id}")
async def leave_waitlist(
    waitlist_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("Customer")),
):
    result = await db.execute(
        select(WaitlistEntry).where(
            WaitlistEntry.id == waitlist_id,
            WaitlistEntry.customer_id == current_user.id,
        )
    )

    entry = result.scalar_one_or_none()

    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Waitlist entry not found.",
        )

    await db.delete(entry)
    await db.commit()

    return {
        "message": "Removed from the waitlist.",
        "waitlist_id": waitlist_id,
    }
