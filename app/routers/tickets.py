import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_roles
from app.auth.security import JWT_SECRET_KEY
from app.database import get_db
from app.models.booking import Booking
from app.models.payment import Payment
from app.models.screen import Screen
from app.models.show import Show
from app.models.ticket import Ticket
from app.models.user import User
from app.models.venue import Venue
from app.services.ticket_service import (
    generate_ticket_pdf,
    get_customer_tickets,
)

router = APIRouter(prefix="/tickets", tags=["Tickets"])


class TicketScanRequest(BaseModel):
    qr_payload: str = Field(min_length=1, max_length=2000)


def _as_utc(value: datetime) -> datetime:
    """Normalize a datetime to UTC, including database values without a timezone."""
    if value.tzinfo is None:
        # MySQL commonly returns timezone-naive datetimes.
        # Treat these stored show times as UTC consistently.
        return value.replace(tzinfo=timezone.utc)

    return value.astimezone(timezone.utc)


@router.get("/my-tickets")
async def list_my_tickets(
    current_user: User = Depends(require_roles("Customer")),
    db: AsyncSession = Depends(get_db),
):
    """List valid tickets belonging to the authenticated customer."""
    return await get_customer_tickets(
        db=db,
        customer_id=current_user.id,
    )


@router.post("/scan")
async def scan_ticket(
    payload: TicketScanRequest,
    current_user: User = Depends(require_roles("Staff")),
    db: AsyncSession = Depends(get_db),
):
    """Validate a ticket QR payload and record a single entry."""

    if current_user.venue_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your Staff account is not assigned to a venue.",
        )

    try:
        qr_data = json.loads(payload.qr_payload)
    except (json.JSONDecodeError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid QR code format.",
        )

    if not isinstance(qr_data, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid QR code format.",
        )

    ticket_code = qr_data.get("ticket_code")
    supplied_signature = qr_data.get("signature")

    if (
        not isinstance(ticket_code, str)
        or not ticket_code.strip()
        or not isinstance(supplied_signature, str)
        or not supplied_signature.strip()
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="QR code is missing required ticket information.",
        )

    expected_signature = hmac.new(
        JWT_SECRET_KEY.encode("utf-8"),
        ticket_code.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected_signature, supplied_signature):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="QR code signature is invalid.",
        )

    result = await db.execute(
        select(Ticket, Booking, Payment, Show, Screen, Venue)
        .join(Booking, Ticket.booking_id == Booking.id)
        .join(Payment, Payment.booking_id == Booking.id)
        .join(Show, Booking.show_id == Show.id)
        .join(Screen, Show.screen_id == Screen.id)
        .join(Venue, Screen.venue_id == Venue.id)
        .where(Ticket.ticket_code == ticket_code)
        .order_by(Payment.id.desc())
    )

    rows = result.unique().all()

    if not rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Ticket not found.",
        )

    ticket, booking, payment, show, screen, venue = rows[0]

    if venue.id != current_user.venue_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This ticket belongs to a different venue.",
        )

    if not hmac.compare_digest(ticket.qr_signature, supplied_signature):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="QR code signature does not match the stored ticket.",
        )

    if ticket.status.lower() != "valid":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Ticket cannot be used. Current status: {ticket.status}.",
        )

    if booking.status.lower() != "confirmed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The booking is not confirmed.",
        )

    if payment.status.lower() != "paid":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Payment for this ticket has not been completed.",
        )

    if show.status.lower() in {"cancelled", "canceled"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This show has been cancelled.",
        )

    now = datetime.now(timezone.utc)
    show_start = _as_utc(show.start_time)
    show_end = _as_utc(show.end_time)

    # Entry opens two hours before the show starts.
    if now < show_start - timedelta(hours=2):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Entry is not open yet. Scanning opens two hours "
                "before the show."
            ),
        )

    if now > show_end:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This show has ended. Entry is closed.",
        )

    if ticket.scanned_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This ticket has already been scanned.",
        )

    ticket.status = "Scanned"
    ticket.scanned_at = now
    ticket.scanned_by_user_id = current_user.id

    await db.commit()
    await db.refresh(ticket)

    return {
        "message": "Ticket validated successfully. Entry permitted.",
        "ticket_id": ticket.id,
        "ticket_code": ticket.ticket_code,
        "booking_reference": booking.booking_reference,
        "show_id": show.id,
        "venue_id": venue.id,
        "venue_name": venue.name,
        "screen_name": screen.name,
        "ticket_status": ticket.status,
        "scanned_at": ticket.scanned_at,
        "scanned_by_user_id": current_user.id,
    }


@router.get("/{ticket_id}/pdf")
async def download_ticket_pdf(
    ticket_id: int,
    current_user: User = Depends(require_roles("Customer")),
    db: AsyncSession = Depends(get_db),
):
    """Download a PDF ticket containing its QR code."""
    pdf_content = await generate_ticket_pdf(
        db=db,
        ticket_id=ticket_id,
        customer_id=current_user.id,
    )

    return Response(
        content=pdf_content,
        media_type="application/pdf",
        headers={
            "Content-Disposition": (
                f'attachment; filename="ticket-{ticket_id}.pdf"'
            )
        },
    )
