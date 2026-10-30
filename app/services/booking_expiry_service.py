
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.models.booking import Booking
from app.models.booked_seat import BookedSeat


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


async def expire_pending_bookings(db: AsyncSession) -> int:
    """Expire pending bookings and release their reserved seat records."""

    now = utc_now()

    result = await db.execute(
        select(Booking)
        .where(
            func.lower(Booking.status) == "pending",
            Booking.expires_at.is_not(None),
            Booking.expires_at <= now,
        )
        .with_for_update()
    )

    expired_bookings = list(result.scalars().all())

    if not expired_bookings:
        return 0

    booking_ids = [booking.id for booking in expired_bookings]

    seats_result = await db.execute(
        select(BookedSeat)
        .where(
            BookedSeat.booking_id.in_(booking_ids),
            func.lower(BookedSeat.status) == "reserved",
        )
        .with_for_update()
    )

    reserved_seats = list(seats_result.scalars().all())

    for booking in expired_bookings:
        booking.status = "Expired"

    for booked_seat in reserved_seats:
        booked_seat.status = "Expired"

    await db.commit()

    print(
        f"Booking expiry job: expired {len(expired_bookings)} "
        f"booking(s) and released {len(reserved_seats)} seat record(s)."
    )

    return len(expired_bookings)


async def run_booking_expiry_job() -> None:
    """Run one expiry pass using a dedicated database session."""

    async with AsyncSessionLocal() as db:
        try:
            await expire_pending_bookings(db)
        except Exception:
            await db.rollback()
            raise
