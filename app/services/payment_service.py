
import secrets
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.booking import Booking
from app.models.booked_seat import BookedSeat
from app.models.payment import Payment


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _is_expired(expires_at: datetime | None, now: datetime) -> bool:
    if expires_at is None:
        return False

    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    return expires_at <= now


async def process_payment(
    db: AsyncSession,
    booking_id: int,
    customer_id: int,
    amount: Decimal,
    payment_method: str,
    simulate_failure: bool = False,
) -> dict:
    """Process a mock payment and confirm the booking on success."""

    try:
        result = await db.execute(
            select(Booking)
            .where(
                Booking.id == booking_id,
                Booking.customer_id == customer_id,
            )
            .with_for_update()
        )
        booking = result.scalar_one_or_none()

        if booking is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Booking not found.",
            )

        if booking.status.lower() != "pending":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Payment is not allowed for a booking with "
                    f"status '{booking.status}'."
                ),
            )

        now = _utc_now()

        if _is_expired(booking.expires_at, now):
            booking.status = "Expired"

            seats_result = await db.execute(
                select(BookedSeat)
                .where(BookedSeat.booking_id == booking.id)
                .with_for_update()
            )

            for seat in seats_result.scalars().all():
                seat.status = "Expired"

            await db.commit()

            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "This booking has expired. "
                    "Please create a new booking."
                ),
            )

        expected_amount = Decimal(str(booking.total_amount)).quantize(
            Decimal("0.01")
        )
        supplied_amount = Decimal(str(amount)).quantize(
            Decimal("0.01")
        )

        if supplied_amount != expected_amount:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "message": (
                        "Payment amount does not match the booking total."
                    ),
                    "expected_amount": str(expected_amount),
                },
            )

        existing_result = await db.execute(
            select(Payment)
            .where(Payment.booking_id == booking.id)
            .with_for_update()
        )
        existing_payments = list(existing_result.scalars().all())

        if any(
            payment.status.lower() == "paid"
            for payment in existing_payments
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This booking has already been paid.",
            )

        seats_result = await db.execute(
            select(BookedSeat)
            .where(BookedSeat.booking_id == booking.id)
            .with_for_update()
        )
        booked_seats = list(seats_result.scalars().all())

        if not booked_seats:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This booking has no reserved seats.",
            )

        for seat in booked_seats:
            if seat.status.lower() != "reserved":
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        "One or more seats are no longer reserved "
                        "for this booking."
                    ),
                )

        transaction_reference = (
            f"TXN-{secrets.token_hex(10).upper()}"
        )

        if simulate_failure:
            payment = Payment(
                booking_id=booking.id,
                transaction_reference=transaction_reference,
                amount=expected_amount,
                payment_method=payment_method,
                status="Failed",
                paid_at=None,
            )

            db.add(payment)
            await db.commit()
            await db.refresh(payment)

            return {
                "payment_id": payment.id,
                "booking_id": booking.id,
                "booking_reference": booking.booking_reference,
                "transaction_reference": payment.transaction_reference,
                "amount": str(payment.amount),
                "payment_method": payment.payment_method,
                "payment_status": payment.status,
                "booking_status": booking.status,
                "paid_at": None,
                "message": (
                    "Mock payment failed. "
                    "The booking remains pending."
                ),
            }

        payment = Payment(
            booking_id=booking.id,
            transaction_reference=transaction_reference,
            amount=expected_amount,
            payment_method=payment_method,
            status="Paid",
            paid_at=now,
        )

        db.add(payment)
        booking.status = "Confirmed"
        booking.confirmed_at = now

        for seat in booked_seats:
            seat.status = "Confirmed"

        await db.commit()
        await db.refresh(payment)
        await db.refresh(booking)

        return {
            "payment_id": payment.id,
            "booking_id": booking.id,
            "booking_reference": booking.booking_reference,
            "transaction_reference": payment.transaction_reference,
            "amount": str(payment.amount),
            "payment_method": payment.payment_method,
            "payment_status": payment.status,
            "booking_status": booking.status,
            "paid_at": (
                payment.paid_at.isoformat()
                if payment.paid_at
                else None
            ),
            "message": "Mock payment successful. Booking confirmed.",
        }

    except HTTPException:
        if db.in_transaction():
            await db.rollback()
        raise

    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "The payment could not be recorded because of a data "
                "conflict. Please check the booking status."
            ),
        ) from exc

    except Exception:
        await db.rollback()
        raise
