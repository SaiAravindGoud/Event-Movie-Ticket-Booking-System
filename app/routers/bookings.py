
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
import secrets
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from fastapi.responses import Response
from pydantic import BaseModel, Field
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.dependencies import require_roles
from app.database import AsyncSessionLocal, get_db
from app.models.booking import Booking
from app.models.cancellation import Cancellation
from app.models.payment import Payment
from app.models.refund import Refund
from app.models.ticket import Ticket
from app.models.user import User
from app.redis_client import get_redis
from app.services.booking_service import create_booking
from app.services.email_service import send_email
from app.services.notification_service import notify_user_background
from app.services.payment_service import process_payment
from app.services.ticket_service import generate_booking_ticket_pdf
from app.services.waitlist_service import (
    notify_waitlist_for_released_seats,
)

router = APIRouter(prefix="/bookings", tags=["Bookings"])


class BookingCreateRequest(BaseModel):
    show_id: int = Field(gt=0)
    seat_ids: list[int] = Field(min_length=1, max_length=10)


class PaymentRequest(BaseModel):
    amount: Decimal = Field(gt=0, max_digits=10, decimal_places=2)
    payment_method: Literal["Mock", "Card", "UPI"] = "Mock"
    simulate_failure: bool = False


def _current_time_for_show(show_start: datetime) -> datetime:
    """Use the same timezone convention as the stored show start time."""
    if show_start.tzinfo is None:
        return datetime.now()
    return datetime.now(show_start.tzinfo)


async def _send_booking_confirmation_email(
    booking_id: int,
    customer_id: int,
    customer_email: str,
) -> None:
    """Generate and email the complete PDF ticket in the background."""
    try:
        async with AsyncSessionLocal() as db:
            pdf_content = await generate_booking_ticket_pdf(
                db=db,
                booking_id=booking_id,
                customer_id=customer_id,
            )

        await send_email(
            recipient=customer_email,
            subject="Booking confirmed - Your tickets",
            body=(
                f"Your booking #{booking_id} has been confirmed. "
                "Your PDF tickets, including QR codes, are attached. "
                "Please present your ticket QR code at the venue."
            ),
            attachment=pdf_content,
            attachment_filename=f"booking-{booking_id}-tickets.pdf",
        )
    except Exception as exc:
        print(
            f"Booking confirmation email failed for booking "
            f"{booking_id}: {type(exc).__name__}: {exc}"
        )


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_booking_endpoint(
    payload: BookingCreateRequest,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
    current_user: User = Depends(require_roles("Customer")),
):
    return await create_booking(
        db=db,
        redis=redis,
        show_id=payload.show_id,
        seat_ids=payload.seat_ids,
        customer_id=current_user.id,
    )


@router.post("/{booking_id}/pay")
async def pay_for_booking(
    booking_id: int,
    payload: PaymentRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("Customer")),
):
    result = await process_payment(
        db=db,
        booking_id=booking_id,
        customer_id=current_user.id,
        amount=payload.amount,
        payment_method=payload.payment_method,
        simulate_failure=payload.simulate_failure,
    )

    if result["payment_status"].lower() == "paid":
        background_tasks.add_task(
            _send_booking_confirmation_email,
            booking_id,
            current_user.id,
            current_user.email,
        )
    else:
        background_tasks.add_task(
            send_email,
            recipient=current_user.email,
            subject="Payment failed - Booking not confirmed",
            body=(
                f"Your payment for booking "
                f"{result['booking_reference']} was unsuccessful. "
                "Your booking remains pending. Please retry payment "
                "before the booking expires."
            ),
        )

    return result


@router.put("/{booking_id}/cancel")
async def cancel_booking(
    booking_id: int,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("Customer")),
):
    """Cancel an eligible paid booking, refund it, and notify the waitlist."""

    result = await db.execute(
        select(Booking)
        .options(
            selectinload(Booking.show),
            selectinload(Booking.booked_seats),
            selectinload(Booking.tickets),
        )
        .where(
            Booking.id == booking_id,
            Booking.customer_id == current_user.id,
        )
        .with_for_update()
    )
    booking = result.scalar_one_or_none()

    if booking is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Booking not found.",
        )

    if booking.status.lower() != "confirmed":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only confirmed bookings can be cancelled.",
        )

    if booking.show.status.lower() in {"cancelled", "canceled"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This show has already been cancelled.",
        )

    now = _current_time_for_show(booking.show.start_time)
    time_until_show = booking.show.start_time - now

    if time_until_show < timedelta(hours=2):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Cancellation is not allowed less than two hours "
                "before the show."
            ),
        )

    if time_until_show > timedelta(hours=24):
        refund_percentage = Decimal("1.00")
    else:
        refund_percentage = Decimal("0.50")

    refund_amount = (
        (Decimal(str(booking.total_amount)) * refund_percentage)
        .quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    )

    payment_result = await db.execute(
        select(Payment)
        .where(
            Payment.booking_id == booking.id,
            func.lower(Payment.status) == "paid",
        )
        .order_by(Payment.id.desc())
        .with_for_update()
    )
    payment = payment_result.scalars().first()

    if payment is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A successful payment was not found for this booking.",
        )

    existing_refund_result = await db.execute(
        select(Refund.id).where(Refund.payment_id == payment.id)
    )
    if existing_refund_result.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A refund record already exists for this payment.",
        )

    released_seat_ids = [
        booked_seat.seat_id for booked_seat in booking.booked_seats
    ]
    released_seat_count = len(released_seat_ids)
    invalidated_ticket_count = len(booking.tickets)
    saved_booking_id = booking.id
    saved_show_id = booking.show_id
    saved_booking_reference = booking.booking_reference
    saved_refund_reference = f"RF-{secrets.token_hex(10).upper()}"
    customer_id = current_user.id
    customer_email = current_user.email

    try:
        cancellation = Cancellation(
            booking_id=booking.id,
            requested_by_user_id=current_user.id,
            reason="Customer Cancellation",
            refund_amount=refund_amount,
            status="Processed",
            processed_at=now,
        )
        db.add(cancellation)

        refund = Refund(
            payment_id=payment.id,
            amount=refund_amount,
            reason="Customer Cancellation",
            status="Processed",
            processed_at=now,
            refund_reference=saved_refund_reference,
        )
        db.add(refund)

        payment.status = "Refunded"
        booking.status = "Cancelled"
        booking.cancelled_at = now

        for booked_seat in booking.booked_seats:
            booked_seat.status = "Cancelled"

        for ticket in booking.tickets:
            ticket.status = "Cancelled"

        await db.commit()

    except Exception:
        await db.rollback()
        raise

    cancellation_message = (
        f"Your booking {saved_booking_reference} has been cancelled. "
        f"Your mock refund of {refund_amount:.2f} "
        f"({int(refund_percentage * 100)}%) has been processed. "
        f"Refund reference: {saved_refund_reference}."
    )

    await notify_user_background(
        background_tasks=background_tasks,
        user_id=customer_id,
        email=customer_email,
        subject="Booking cancellation and refund confirmation",
        message=cancellation_message,
        notification_type="booking_cancelled",
        data={
            "booking_id": saved_booking_id,
            "booking_reference": saved_booking_reference,
            "show_id": saved_show_id,
            "refund_amount": str(refund_amount),
            "refund_percentage": int(refund_percentage * 100),
            "refund_reference": saved_refund_reference,
        },
    )

    try:
        waitlist_result = await notify_waitlist_for_released_seats(
            db=db,
            show_id=saved_show_id,
            released_seat_ids=released_seat_ids,
            background_tasks=background_tasks,
        )
    except Exception as exc:
        await db.rollback()
        print(
            f"Waitlist processing failed for show {saved_show_id}: "
            f"{type(exc).__name__}: {exc}"
        )
        waitlist_result = {"notified_count": 0, "notified_entries": []}

    return {
        "message": "Booking cancelled and mock refund recorded.",
        "booking_id": saved_booking_id,
        "booking_reference": saved_booking_reference,
        "booking_status": "Cancelled",
        "refund": {
            "amount": str(refund_amount),
            "percentage": int(refund_percentage * 100),
            "reason": "Customer Cancellation",
            "status": "Processed",
            "refund_reference": saved_refund_reference,
        },
        "released_seats": released_seat_count,
        "invalidated_tickets": invalidated_ticket_count,
        "waitlist_notifications": waitlist_result["notified_count"],
    }


@router.get("/{booking_id}/refunds")
async def get_booking_refunds(
    booking_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("Customer")),
):
    """List refund records for a booking owned by the signed-in customer."""

    booking_result = await db.execute(
        select(Booking.id).where(
            Booking.id == booking_id,
            Booking.customer_id == current_user.id,
        )
    )
    owned_booking_id = booking_result.scalar_one_or_none()

    if owned_booking_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Booking not found.",
        )

    result = await db.execute(
        select(Refund, Payment)
        .join(Payment, Refund.payment_id == Payment.id)
        .where(Payment.booking_id == booking_id)
        .order_by(Refund.id.desc())
    )

    rows = result.all()

    return {
        "booking_id": booking_id,
        "refunds": [
            {
                "refund_id": refund.id,
                "payment_id": payment.id,
                "amount": str(refund.amount),
                "reason": refund.reason,
                "status": refund.status,
                "refunded_at": refund.processed_at,
                "refund_reference": refund.refund_reference,
            }
            for refund, payment in rows
        ],
    }


@router.get("/{booking_id}/ticket")
async def download_booking_ticket_pdf(
    booking_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("Customer")),
):
    """Download a PDF containing all tickets for one paid booking."""

    pdf_content = await generate_booking_ticket_pdf(
        db=db,
        booking_id=booking_id,
        customer_id=current_user.id,
    )

    return Response(
        content=pdf_content,
        media_type="application/pdf",
        headers={
            "Content-Disposition": (
                f'attachment; filename="booking-{booking_id}-tickets.pdf"'
            )
        },
    )
