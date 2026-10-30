
from datetime import datetime, timedelta
from decimal import Decimal
import secrets

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.dependencies import get_current_user
from app.database import get_db
from app.models.booking import Booking
from app.models.booked_seat import BookedSeat
from app.models.cancellation import Cancellation
from app.models.event import Event
from app.models.payment import Payment
from app.models.refund import Refund
from app.models.screen import Screen
from app.models.seat import Seat
from app.models.show import Show
from app.models.show_category_price import ShowCategoryPrice
from app.models.ticket import Ticket
from app.models.user import User
from app.schemas.show import ShowCreate, ShowResponse, ShowUpdate
from app.services.notification_service import notify_user_background
from app.routers.events import invalidate_event_cache


router = APIRouter(tags=["Shows"])

CLEANING_GAP = timedelta(minutes=15)
BOOKING_CLOSURE = timedelta(minutes=30)

CONFIRMED_BOOKING_STATUSES = {
    "confirmed",
    "paid",
    "completed",
    "booked",
}


def ensure_event_manager(user, event=None):
    """Allow Admins and approved Organizers to manage their own events."""

    role = getattr(user, "role", None)

    if role == "Admin":
        return

    if role != "Organizer":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only Admins and approved Organizers can manage shows.",
        )

    if not getattr(user, "organizer_approved", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Organizer approval is required.",
        )

    if event is not None and event.organizer_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can manage only your own events and shows.",
        )


async def get_event_or_404(db: AsyncSession, event_id: int) -> Event:
    result = await db.execute(
        select(Event).where(Event.id == event_id)
    )
    event = result.scalar_one_or_none()

    if event is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Event not found.",
        )

    return event


async def get_show_or_404(db: AsyncSession, show_id: int) -> Show:
    result = await db.execute(
        select(Show)
        .options(selectinload(Show.category_prices))
        .where(Show.id == show_id)
    )
    show = result.scalar_one_or_none()

    if show is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Show not found.",
        )

    return show


async def get_screen_with_seats(
    db: AsyncSession,
    screen_id: int,
) -> Screen:
    """Load seats eagerly to avoid asynchronous lazy-loading errors."""

    result = await db.execute(
        select(Screen)
        .options(selectinload(Screen.seats))
        .where(Screen.id == screen_id)
    )
    screen = result.scalar_one_or_none()

    if screen is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Screen not found.",
        )

    return screen


async def validate_screen_prices(
    db: AsyncSession,
    screen_id: int,
    prices,
) -> None:
    """Require prices for all active seat categories on the screen."""

    screen = await get_screen_with_seats(db, screen_id)

    required_categories = {
        seat.category for seat in screen.seats if seat.is_active
    }
    supplied_categories = {item.category for item in prices}
    missing_categories = required_categories - supplied_categories

    if missing_categories:
        missing = ", ".join(sorted(missing_categories))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "A price is required for every active seat category. "
                f"Missing: {missing}."
            ),
        )


async def validate_schedule(
    db: AsyncSession,
    screen_id: int,
    start_at: datetime,
    end_at: datetime,
    exclude_show_id: int | None = None,
) -> None:
    """Reject overlapping shows and enforce a 15-minute cleaning gap."""

    result = await db.execute(
        select(Show).where(
            Show.screen_id == screen_id,
            Show.status == "Scheduled",
        )
    )
    existing_shows = result.scalars().all()

    proposed_start_with_gap = start_at - CLEANING_GAP
    proposed_end_with_gap = end_at + CLEANING_GAP

    for existing in existing_shows:
        if exclude_show_id is not None and existing.id == exclude_show_id:
            continue

        if (
            proposed_start_with_gap < existing.end_time
            and proposed_end_with_gap > existing.start_time
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "This show conflicts with another scheduled show "
                    "on the same screen. A 15-minute cleaning gap is required."
                ),
            )


async def has_confirmed_booking(
    db: AsyncSession,
    show_id: int,
) -> bool:
    """Check whether the show has a confirmed booking."""

    result = await db.execute(
        select(Booking.id)
        .where(
            Booking.show_id == show_id,
            func.lower(Booking.status).in_(
                list(CONFIRMED_BOOKING_STATUSES)
            ),
        )
        .limit(1)
    )
    return result.scalar_one_or_none() is not None


def show_response_data(show: Show) -> dict:
    """Build the existing show response, including category prices."""

    return {
        "id": show.id,
        "event_id": show.event_id,
        "screen_id": show.screen_id,
        "start_time": show.start_time,
        "end_time": show.end_time,
        "base_price": show.base_price,
        "silver_price": show.silver_price,
        "gold_price": show.gold_price,
        "recliner_price": show.recliner_price,
        "booking_closes_at": show.booking_closes_at,
        "status": show.status,
        "prices": [
            {"category": item.category, "price": item.price}
            for item in show.category_prices
        ],
    }


async def replace_prices(
    db: AsyncSession,
    show: Show,
    prices,
) -> None:
    """Replace category prices and synchronize legacy price columns."""

    price_map = {
        item.category: Decimal(str(item.price))
        for item in prices
    }

    for category_price in list(show.category_prices):
        await db.delete(category_price)

    await db.flush()

    for category, price in price_map.items():
        db.add(
            ShowCategoryPrice(
                show_id=show.id,
                category=category,
                price=price,
            )
        )

    show.silver_price = price_map.get("Silver", Decimal("0.00"))
    show.gold_price = price_map.get("Gold", Decimal("0.00"))
    show.recliner_price = price_map.get("Recliner", Decimal("0.00"))

    if price_map:
        show.base_price = min(price_map.values())


@router.post(
    "/events/{event_id}/shows",
    response_model=ShowResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_show(
    event_id: int,
    payload: ShowCreate,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    event = await get_event_or_404(db, event_id)
    ensure_event_manager(current_user, event)

    if event.status != "Published":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Shows can be created only for published events.",
        )

    start_at = datetime.combine(payload.show_date, payload.start_time)

    if start_at <= datetime.now():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Shows must be scheduled for a future date and time.",
        )

    end_at = start_at + timedelta(minutes=event.duration_minutes)
    booking_closes_at = start_at - BOOKING_CLOSURE

    await validate_screen_prices(db, payload.screen_id, payload.prices)
    await validate_schedule(db, payload.screen_id, start_at, end_at)

    price_map = {
        item.category: Decimal(str(item.price))
        for item in payload.prices
    }

    show = Show(
        event_id=event.id,
        screen_id=payload.screen_id,
        start_time=start_at,
        end_time=end_at,
        booking_closes_at=booking_closes_at,
        status="Scheduled",
        base_price=min(price_map.values()),
        silver_price=price_map.get("Silver", Decimal("0.00")),
        gold_price=price_map.get("Gold", Decimal("0.00")),
        recliner_price=price_map.get("Recliner", Decimal("0.00")),
    )

    db.add(show)
    await db.flush()

    for category, price in price_map.items():
        db.add(
            ShowCategoryPrice(
                show_id=show.id,
                category=category,
                price=price,
            )
        )

    await db.commit()

    await invalidate_event_cache()

    saved_show = await get_show_or_404(db, show.id)
    return show_response_data(saved_show)


@router.get(
    "/events/{event_id}/shows",
    response_model=list[ShowResponse],
)
async def list_event_shows(
    event_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    event = await get_event_or_404(db, event_id)
    role = getattr(current_user, "role", None)

    if role == "Customer":
        if event.status != "Published":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Event not found.",
            )
    else:
        ensure_event_manager(current_user, event)

    result = await db.execute(
        select(Show)
        .options(selectinload(Show.category_prices))
        .where(Show.event_id == event_id)
        .order_by(Show.start_time)
    )

    return [
        show_response_data(show)
        for show in result.scalars().all()
    ]


@router.put(
    "/shows/{show_id}",
    response_model=ShowResponse,
)
async def update_show(
    show_id: int,
    payload: ShowUpdate,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    show = await get_show_or_404(db, show_id)
    event = await get_event_or_404(db, show.event_id)
    ensure_event_manager(current_user, event)

    if show.status != "Scheduled":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only scheduled shows can be updated.",
        )

    new_screen_id = (
        payload.screen_id
        if payload.screen_id is not None
        else show.screen_id
    )

    old_start = show.start_time
    new_date = payload.show_date or old_start.date()
    new_time = payload.start_time or old_start.time()
    new_start = datetime.combine(new_date, new_time)
    new_start = new_start.replace(tzinfo=None)
    new_end = new_start + timedelta(minutes=event.duration_minutes)

    if new_start <= datetime.now():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Shows must be scheduled for a future date and time.",
        )

    effective_prices = payload.prices

    if effective_prices is None:
        effective_prices = [
            type(
                "PriceItem",
                (),
                {"category": item.category, "price": item.price},
            )()
            for item in show.category_prices
        ]

    await validate_screen_prices(db, new_screen_id, effective_prices)
    await validate_schedule(
        db,
        new_screen_id,
        new_start,
        new_end,
        exclude_show_id=show.id,
    )

    if payload.prices is not None:
        if await has_confirmed_booking(db, show.id):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Prices cannot change after a confirmed booking.",
            )

        await replace_prices(db, show, payload.prices)

    show.screen_id = new_screen_id
    show.start_time = new_start
    show.end_time = new_end
    show.booking_closes_at = new_start - BOOKING_CLOSURE

    await db.commit()

    await invalidate_event_cache()

    saved_show = await get_show_or_404(db, show.id)
    return show_response_data(saved_show)


@router.put(
    "/shows/{show_id}/cancel",
    response_model=ShowResponse,
)
async def cancel_show(
    show_id: int,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Cancel a scheduled show and issue full mock refunds for its
    confirmed bookings. Queue customer emails after committing.
    """

    show = await get_show_or_404(db, show_id)
    event = await get_event_or_404(db, show.event_id)
    ensure_event_manager(current_user, event)

    if show.status != "Scheduled":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only scheduled shows can be cancelled.",
        )

    customer_notifications = []

    try:
        locked_result = await db.execute(
            select(Show)
            .where(Show.id == show_id)
            .with_for_update()
        )
        locked_show = locked_result.scalar_one_or_none()

        if locked_show is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Show not found.",
            )

        if locked_show.status != "Scheduled":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only scheduled shows can be cancelled.",
            )

        booking_result = await db.execute(
            select(Booking)
            .where(
                Booking.show_id == show_id,
                func.lower(Booking.status).in_(
                    list(CONFIRMED_BOOKING_STATUSES)
                ),
            )
            .with_for_update()
        )
        bookings = booking_result.scalars().all()

        now = datetime.now()

        for booking in bookings:
            payment_result = await db.execute(
                select(Payment)
                .where(
                    Payment.booking_id == booking.id,
                    func.lower(Payment.status) == "paid",
                )
                .order_by(Payment.id.desc())
                .with_for_update()
            )
            payments = payment_result.scalars().all()

            booking_refund_total = Decimal("0.00")

            for payment in payments:
                existing_refund_result = await db.execute(
                    select(Refund.amount).where(
                        Refund.payment_id == payment.id
                    )
                )
                existing_refund_amounts = (
                    existing_refund_result.scalars().all()
                )

                if existing_refund_amounts:
                    booking_refund_total += sum(
                        (
                            Decimal(str(amount))
                            for amount in existing_refund_amounts
                        ),
                        Decimal("0.00"),
                    )
                    payment.status = "Refunded"
                    continue

                refund_amount = Decimal(str(payment.amount)).quantize(
                    Decimal("0.01")
                )

                db.add(
                    Refund(
                        payment_id=payment.id,
                        amount=refund_amount,
                        reason="Show Cancellation",
                        status="Processed",
                        processed_at=now,
                        refund_reference=(
                            f"RF-{secrets.token_hex(12).upper()}"
                        ),
                    )
                )

                payment.status = "Refunded"
                booking_refund_total += refund_amount

            seat_result = await db.execute(
                select(BookedSeat).where(
                    BookedSeat.booking_id == booking.id
                )
            )

            for booked_seat in seat_result.scalars().all():
                booked_seat.status = "Cancelled"

            ticket_result = await db.execute(
                select(Ticket).where(
                    Ticket.booking_id == booking.id
                )
            )

            for ticket in ticket_result.scalars().all():
                ticket.status = "Cancelled"

            booking.status = "Cancelled"
            booking.cancelled_at = now

            db.add(
                Cancellation(
                    booking_id=booking.id,
                    requested_by_user_id=current_user.id,
                    reason="Show Cancellation",
                    refund_amount=booking_refund_total,
                    status="Processed",
                    processed_at=now,
                )
            )

            customer_result = await db.execute(
                select(User).where(User.id == booking.customer_id)
            )
            customer = customer_result.scalar_one_or_none()

            if customer is not None:
                customer_notifications.append(
                    {
                        "user_id": customer.id,
                        "email": customer.email,
                        "booking_id": booking.id,
                        "booking_reference": booking.booking_reference,
                        "refund_amount": booking_refund_total,
                    }
                )

        locked_show.status = "Cancelled"
        await db.commit()

    except Exception:
        await db.rollback()
        raise

    # Invalidate event list/detail cache after the cancellation is committed.
    await invalidate_event_cache()

    # Queue emails and send WebSocket notifications after commit.
    for notification in customer_notifications:
        refund_amount = notification["refund_amount"]

        message = (
            f"Show ID {show_id} has been cancelled by the organizer. "
            f"Your booking {notification['booking_reference']} "
            f"has also been cancelled. "
            f"Your full recorded mock refund amount is "
            f"{refund_amount:.2f}. "
            "Please check your booking and refund details in the system."
        )

        try:
            await notify_user_background(
                background_tasks=background_tasks,
                user_id=notification["user_id"],
                email=notification["email"],
                subject="Show cancelled - full refund information",
                message=message,
                notification_type="show_cancelled",
                data={
                    "show_id": show_id,
                    "booking_id": notification["booking_id"],
                    "booking_reference": notification["booking_reference"],
                    "refund_amount": str(refund_amount),
                    "refund_reason": "Show Cancellation",
                },
            )
        except Exception as exc:
            print(
                f"Show cancellation notification failed for booking "
                f"{notification['booking_id']}: "
                f"{type(exc).__name__}: {exc}"
            )

    saved_show = await get_show_or_404(db, show_id)
    return show_response_data(saved_show)
