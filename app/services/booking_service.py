
import secrets
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import HTTPException, status
from redis.asyncio import Redis
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.booking import Booking
from app.models.booked_seat import BookedSeat
from app.models.seat import Seat
from app.models.show_category_price import ShowCategoryPrice
from app.services.seat_lock_service import (
    get_show_or_404,
    seat_lock_key,
)

BOOKING_PENDING_SECONDS = 600


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _seat_price(
    category: str,
    show,
    category_prices: dict[str, Decimal],
) -> Decimal:
    if category in category_prices:
        price = category_prices[category]
    else:
        fields = {
            "Silver": "silver_price",
            "Gold": "gold_price",
            "Recliner": "recliner_price",
        }
        field = fields.get(category)
        price = getattr(show, field) if field else None

        if price is None or Decimal(str(price)) <= 0:
            price = show.base_price

    price = Decimal(str(price)).quantize(Decimal("0.01"))

    if price <= 0:
        raise HTTPException(
            status_code=400,
            detail=f"No valid price is configured for seat category '{category}'.",
        )

    return price


async def _verify_and_refresh_locks(
    redis: Redis,
    keys: list[str],
    customer_id: int,
) -> None:
    script = """
    local owner = ARGV[1]
    local ttl = tonumber(ARGV[2])

    for i, key in ipairs(KEYS) do
        if redis.call('GET', key) ~= owner then
            return i
        end
    end

    for i, key in ipairs(KEYS) do
        redis.call('EXPIRE', key, ttl)
    end

    return 0
    """

    try:
        result = await redis.eval(
            script,
            len(keys),
            *keys,
            str(customer_id),
            BOOKING_PENDING_SECONDS,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Seat-lock service is temporarily unavailable.",
        ) from exc

    if int(result) != 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "A selected seat is not locked by you or its lock has expired. "
                "Check availability and lock the seats again."
            ),
        )


async def create_booking(
    db: AsyncSession,
    redis: Redis,
    show_id: int,
    seat_ids: list[int],
    customer_id: int,
) -> dict:
    if not seat_ids:
        raise HTTPException(status_code=422, detail="Select at least one seat.")

    if len(seat_ids) > 10:
        raise HTTPException(
            status_code=422,
            detail="A booking cannot contain more than 10 seats.",
        )

    if len(seat_ids) != len(set(seat_ids)):
        raise HTTPException(
            status_code=422,
            detail="Duplicate seat IDs are not allowed.",
        )

    keys = [
        seat_lock_key(show_id, seat_id)
        for seat_id in sorted(seat_ids)
    ]

    booking_saved = False

    try:
        show = await get_show_or_404(db, show_id)

        # Acquire database row locks in a consistent order. Competing
        # transactions selecting the same seats must wait for one another.
        seats_result = await db.execute(
            select(Seat)
            .where(
                Seat.screen_id == show.screen_id,
                Seat.is_active.is_(True),
                Seat.id.in_(seat_ids),
            )
            .order_by(Seat.id)
            .with_for_update()
        )
        seats = list(seats_result.scalars().all())
        seats_by_id = {seat.id: seat for seat in seats}

        if len(seats_by_id) != len(seat_ids):
            raise HTTPException(
                status_code=400,
                detail=(
                    "One or more selected seats are invalid, inactive, "
                    "or belong to another screen."
                ),
            )

        # Check again after acquiring the database row locks.
        await _verify_and_refresh_locks(redis, keys, customer_id)

        now = _utc_now()

        # A locking read is important here: it checks the latest committed
        # reservation state even if this transaction waited for another one.
        existing_result = await db.execute(
            select(BookedSeat.seat_id)
            .join(Booking, Booking.id == BookedSeat.booking_id)
            .where(
                Booking.show_id == show_id,
                BookedSeat.seat_id.in_(seat_ids),
                func.lower(BookedSeat.status).in_(
                    ["reserved", "confirmed", "paid", "booked", "checked_in"]
                ),
                or_(
                    func.lower(Booking.status).in_(
                        ["confirmed", "paid", "completed", "booked"]
                    ),
                    (
                        (func.lower(Booking.status) == "pending")
                        & (Booking.expires_at.is_not(None))
                        & (Booking.expires_at > now)
                    ),
                ),
            )
            .with_for_update()
        )
        existing_ids = set(existing_result.scalars().all())

        if existing_ids:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "message": "One or more selected seats are already reserved.",
                    "seat_ids": sorted(existing_ids),
                },
            )

        prices_result = await db.execute(
            select(ShowCategoryPrice).where(
                ShowCategoryPrice.show_id == show_id
            )
        )
        category_prices = {
            item.category: Decimal(str(item.price))
            for item in prices_result.scalars().all()
        }

        seat_prices = {
            seat.id: _seat_price(seat.category, show, category_prices)
            for seat in seats
        }
        total_amount = sum(seat_prices.values(), Decimal("0.00"))

        booking = Booking(
            booking_reference=f"BK-{secrets.token_hex(8).upper()}",
            customer_id=customer_id,
            show_id=show_id,
            total_amount=total_amount,
            status="Pending",
            expires_at=now + timedelta(seconds=BOOKING_PENDING_SECONDS),
        )

        db.add(booking)
        await db.flush()

        for seat_id in seat_ids:
            db.add(
                BookedSeat(
                    booking_id=booking.id,
                    seat_id=seat_id,
                    seat_price=seat_prices[seat_id],
                    status="Reserved",
                )
            )

        await db.commit()
        booking_saved = True
        await db.refresh(booking)

    except HTTPException:
        if not booking_saved:
            await db.rollback()
        raise
    except Exception:
        if not booking_saved:
            await db.rollback()
        raise

    # Database reservation is committed. Release only locks still owned
    # by this customer; if Redis fails, remaining locks expire automatically.
    try:
        await redis.eval(
            """
            local owner = ARGV[1]
            local released = 0

            for i, key in ipairs(KEYS) do
                if redis.call('GET', key) == owner then
                    redis.call('DEL', key)
                    released = released + 1
                end
            end

            return released
            """,
            len(keys),
            *keys,
            str(customer_id),
        )
    except Exception:
        pass

    return {
        "booking_id": booking.id,
        "booking_reference": booking.booking_reference,
        "show_id": booking.show_id,
        "seat_ids": seat_ids,
        "total_seats": len(seat_ids),
        "total_amount": str(booking.total_amount),
        "status": booking.status,
        "expires_at": (
            booking.expires_at.isoformat()
            if booking.expires_at
            else None
        ),
        "message": "Pending booking created. Complete payment before it expires.",
    }
