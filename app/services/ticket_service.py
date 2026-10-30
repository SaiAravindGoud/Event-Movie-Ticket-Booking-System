
import hashlib
import hmac
import io
import json
import secrets
from xml.sax.saxutils import escape

import qrcode
from fastapi import HTTPException
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    Image as PDFImage,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.security import JWT_SECRET_KEY
from app.models.booking import Booking
from app.models.booked_seat import BookedSeat
from app.models.event import Event
from app.models.payment import Payment
from app.models.screen import Screen
from app.models.seat import Seat
from app.models.show import Show
from app.models.ticket import Ticket
from app.models.venue import Venue


def _create_qr_signature(ticket_code: str) -> str:
    return hmac.new(
        JWT_SECRET_KEY.encode("utf-8"),
        ticket_code.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _create_qr_payload(ticket: Ticket) -> str:
    return json.dumps(
        {
            "ticket_code": ticket.ticket_code,
            "signature": ticket.qr_signature,
        },
        separators=(",", ":"),
    )


async def _ensure_booking_tickets(
    db: AsyncSession,
    booking: Booking,
) -> None:
    """Create a ticket for every booked seat when one is missing."""
    seat_result = await db.execute(
        select(BookedSeat).where(
            BookedSeat.booking_id == booking.id
        )
    )
    booked_seats = list(seat_result.scalars().all())

    existing_result = await db.execute(
        select(Ticket.booked_seat_id).where(
            Ticket.booking_id == booking.id
        )
    )
    existing_seat_ids = set(existing_result.scalars().all())

    for booked_seat in booked_seats:
        if booked_seat.id in existing_seat_ids:
            continue

        ticket = Ticket(
            booking_id=booking.id,
            booked_seat_id=booked_seat.id,
            ticket_code=f"TKT-{secrets.token_hex(12).upper()}",
            qr_signature="pending",
            status="Valid",
        )
        ticket.qr_signature = _create_qr_signature(
            ticket.ticket_code
        )
        db.add(ticket)

    await db.flush()


async def _get_paid_confirmed_bookings(
    db: AsyncSession,
    customer_id: int,
) -> list[Booking]:
    """Return confirmed bookings that have a successful payment."""
    result = await db.execute(
        select(Booking).where(
            Booking.customer_id == customer_id,
            func.lower(Booking.status) == "confirmed",
            Booking.id.in_(
                select(Payment.booking_id).where(
                    func.lower(Payment.status) == "paid"
                )
            ),
        )
    )
    return list(result.scalars().unique().all())


async def get_customer_tickets(
    db: AsyncSession,
    customer_id: int,
) -> list[dict]:
    """Return valid tickets for the customer's paid bookings."""
    try:
        bookings = await _get_paid_confirmed_bookings(
            db,
            customer_id,
        )

        for booking in bookings:
            await _ensure_booking_tickets(db, booking)

        await db.commit()

        result = await db.execute(
            select(Ticket, Booking, BookedSeat, Seat)
            .join(Booking, Ticket.booking_id == Booking.id)
            .join(
                BookedSeat,
                Ticket.booked_seat_id == BookedSeat.id,
            )
            .join(Seat, BookedSeat.seat_id == Seat.id)
            .where(
                Booking.customer_id == customer_id,
                func.lower(Booking.status) == "confirmed",
                Booking.id.in_(
                    select(Payment.booking_id).where(
                        func.lower(Payment.status) == "paid"
                    )
                ),
                func.lower(Ticket.status) == "valid",
            )
            .order_by(Ticket.id.desc())
        )

        rows = result.unique().all()

        return [
            {
                "ticket_id": ticket.id,
                "ticket_code": ticket.ticket_code,
                "booking_id": booking.id,
                "booking_reference": booking.booking_reference,
                "show_id": booking.show_id,
                "seat": f"{seat.row_label}{seat.seat_number}",
                "seat_category": seat.category,
                "status": ticket.status,
                "qr_payload": _create_qr_payload(ticket),
                "pdf_endpoint": f"/tickets/{ticket.id}/pdf",
            }
            for ticket, booking, booked_seat, seat in rows
        ]

    except Exception:
        await db.rollback()
        raise


async def _get_customer_ticket_row(
    db: AsyncSession,
    ticket_id: int,
    customer_id: int,
):
    """Load a single ticket owned by the customer."""
    result = await db.execute(
        select(
            Ticket,
            Booking,
            BookedSeat,
            Seat,
            Show,
            Event,
            Screen,
            Venue,
        )
        .join(Booking, Ticket.booking_id == Booking.id)
        .join(
            BookedSeat,
            Ticket.booked_seat_id == BookedSeat.id,
        )
        .join(Seat, BookedSeat.seat_id == Seat.id)
        .join(Show, Booking.show_id == Show.id)
        .join(Event, Show.event_id == Event.id)
        .join(Screen, Show.screen_id == Screen.id)
        .join(Venue, Screen.venue_id == Venue.id)
        .where(
            Ticket.id == ticket_id,
            Booking.customer_id == customer_id,
            func.lower(Booking.status) == "confirmed",
            Booking.id.in_(
                select(Payment.booking_id).where(
                    func.lower(Payment.status) == "paid"
                )
            ),
        )
    )

    return result.first()


def _build_ticket_story(
    ticket: Ticket,
    booking: Booking,
    seat: Seat,
    show: Show,
    event: Event,
    screen: Screen,
    venue: Venue,
    styles,
    title_style,
    event_style,
    include_booking_summary: bool = False,
    paid_amount=None,
):
    """Build the PDF content for one ticket."""
    signature = _create_qr_signature(ticket.ticket_code)

    if not hmac.compare_digest(signature, ticket.qr_signature):
        raise HTTPException(
            status_code=409,
            detail="Ticket QR signature validation failed.",
        )

    qr_image = qrcode.make(_create_qr_payload(ticket))
    qr_buffer = io.BytesIO()
    qr_image.save(qr_buffer, format="PNG")
    qr_buffer.seek(0)

    show_start = show.start_time.strftime("%d %B %Y, %I:%M %p")
    show_end = show.end_time.strftime("%d %B %Y, %I:%M %p")

    story = [
        Paragraph("EVENT &amp; MOVIE TICKET", title_style),
        Spacer(1, 10),
        Paragraph(escape(event.title), event_style),
        Spacer(1, 14),
    ]

    if include_booking_summary:
        story.extend(
            [
                Paragraph(
                    f"<b>Booking Reference:</b> "
                    f"{escape(booking.booking_reference)}",
                    styles["Normal"],
                ),
                Spacer(1, 7),
            ]
        )

        if paid_amount is not None:
            story.extend(
                [
                    Paragraph(
                        f"<b>Total Paid:</b> "
                        f"Rs. {escape(str(paid_amount))}",
                        styles["Normal"],
                    ),
                    Spacer(1, 7),
                ]
            )

    story.extend(
        [
            Paragraph(
                f"<b>Event Category:</b> "
                f"{escape(event.category or 'Not specified')}",
                styles["Normal"],
            ),
            Spacer(1, 7),
            Paragraph(
                f"<b>Language:</b> "
                f"{escape(event.language or 'Not specified')}",
                styles["Normal"],
            ),
            Spacer(1, 7),
            Paragraph(
                f"<b>Booking Reference:</b> "
                f"{escape(booking.booking_reference)}",
                styles["Normal"],
            ),
            Spacer(1, 7),
            Paragraph(
                f"<b>Ticket Code:</b> "
                f"{escape(ticket.ticket_code)}",
                styles["Normal"],
            ),
            Spacer(1, 7),
            Paragraph(
                f"<b>Show Date and Time:</b> "
                f"{escape(show_start)}",
                styles["Normal"],
            ),
            Spacer(1, 7),
            Paragraph(
                f"<b>Show Ends:</b> {escape(show_end)}",
                styles["Normal"],
            ),
            Spacer(1, 7),
            Paragraph(
                f"<b>Venue:</b> {escape(venue.name)}",
                styles["Normal"],
            ),
            Spacer(1, 7),
            Paragraph(
                f"<b>Address:</b> "
                f"{escape(venue.address or 'Not specified')}, "
                f"{escape(venue.city or '')}",
                styles["Normal"],
            ),
            Spacer(1, 7),
            Paragraph(
                f"<b>Screen:</b> {escape(screen.name)}",
                styles["Normal"],
            ),
            Spacer(1, 7),
            Paragraph(
                f"<b>Seat:</b> {escape(seat.row_label)}"
                f"{escape(str(seat.seat_number))}",
                styles["Normal"],
            ),
            Spacer(1, 7),
            Paragraph(
                f"<b>Seat Category:</b> "
                f"{escape(seat.category or 'Standard')}",
                styles["Normal"],
            ),
            Spacer(1, 7),
            Paragraph(
                f"<b>Ticket Status:</b> "
                f"{escape(ticket.status)}",
                styles["Normal"],
            ),
            Spacer(1, 18),
            Paragraph(
                "Scan this QR code for ticket verification",
                styles["Normal"],
            ),
            Spacer(1, 10),
            PDFImage(
                qr_buffer,
                width=2 * inch,
                height=2 * inch,
            ),
        ]
    )

    return story


async def generate_ticket_pdf(
    db: AsyncSession,
    ticket_id: int,
    customer_id: int,
) -> bytes:
    """Generate one ticket PDF for a paid, confirmed booking."""
    row = await _get_customer_ticket_row(
        db,
        ticket_id,
        customer_id,
    )

    if row is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "Ticket not found or you do not have permission "
                "to access it."
            ),
        )

    (
        ticket,
        booking,
        booked_seat,
        seat,
        show,
        event,
        screen,
        venue,
    ) = row

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "TicketTitle",
        parent=styles["Title"],
        alignment=TA_CENTER,
        textColor=colors.HexColor("#17365D"),
    )
    event_style = ParagraphStyle(
        "EventTitle",
        parent=styles["Heading2"],
        alignment=TA_CENTER,
    )

    story = _build_ticket_story(
        ticket,
        booking,
        seat,
        show,
        event,
        screen,
        venue,
        styles,
        title_style,
        event_style,
    )

    pdf_buffer = io.BytesIO()
    document = SimpleDocTemplate(
        pdf_buffer,
        pagesize=letter,
        rightMargin=0.7 * inch,
        leftMargin=0.7 * inch,
        topMargin=0.6 * inch,
        bottomMargin=0.6 * inch,
        title=f"Ticket {ticket.ticket_code}",
    )
    document.build(story)

    return pdf_buffer.getvalue()


async def generate_booking_ticket_pdf(
    db: AsyncSession,
    booking_id: int,
    customer_id: int,
) -> bytes:
    """Generate one PDF containing all tickets for a paid booking."""
    booking_result = await db.execute(
        select(Booking).where(
            Booking.id == booking_id,
            Booking.customer_id == customer_id,
            func.lower(Booking.status) == "confirmed",
            Booking.id.in_(
                select(Payment.booking_id).where(
                    func.lower(Payment.status) == "paid"
                )
            ),
        )
    )
    booking = booking_result.scalar_one_or_none()

    if booking is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "Paid booking not found or you do not have "
                "permission to access it."
            ),
        )

    try:
        await _ensure_booking_tickets(db, booking)
        await db.commit()

        payment_result = await db.execute(
            select(Payment)
            .where(
                Payment.booking_id == booking_id,
                func.lower(Payment.status) == "paid",
            )
            .order_by(Payment.id.desc())
            .limit(1)
        )
        payment = payment_result.scalars().first()

        if payment is None:
            raise HTTPException(
                status_code=404,
                detail="Successful payment not found.",
            )

        # Join Booking explicitly before referring to Booking.show_id.
        result = await db.execute(
            select(
                Ticket,
                BookedSeat,
                Seat,
                Show,
                Event,
                Screen,
                Venue,
            )
            .join(Booking, Ticket.booking_id == Booking.id)
            .join(
                BookedSeat,
                Ticket.booked_seat_id == BookedSeat.id,
            )
            .join(Seat, BookedSeat.seat_id == Seat.id)
            .join(Show, Booking.show_id == Show.id)
            .join(Event, Show.event_id == Event.id)
            .join(Screen, Show.screen_id == Screen.id)
            .join(Venue, Screen.venue_id == Venue.id)
            .where(
                Ticket.booking_id == booking_id,
                Booking.customer_id == customer_id,
            )
            .order_by(Ticket.id)
        )

        rows = result.unique().all()

        if not rows:
            raise HTTPException(
                status_code=404,
                detail="No tickets were found for this booking.",
            )

        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            "BookingTicketTitle",
            parent=styles["Title"],
            alignment=TA_CENTER,
            textColor=colors.HexColor("#17365D"),
        )
        event_style = ParagraphStyle(
            "BookingEventTitle",
            parent=styles["Heading2"],
            alignment=TA_CENTER,
        )

        story = []

        for index, row in enumerate(rows):
            (
                ticket,
                booked_seat,
                seat,
                show,
                event,
                screen,
                venue,
            ) = row

            if index > 0:
                story.append(PageBreak())

            story.extend(
                _build_ticket_story(
                    ticket,
                    booking,
                    seat,
                    show,
                    event,
                    screen,
                    venue,
                    styles,
                    title_style,
                    event_style,
                    include_booking_summary=True,
                    paid_amount=payment.amount,
                )
            )

        pdf_buffer = io.BytesIO()
        document = SimpleDocTemplate(
            pdf_buffer,
            pagesize=letter,
            rightMargin=0.7 * inch,
            leftMargin=0.7 * inch,
            topMargin=0.6 * inch,
            bottomMargin=0.6 * inch,
            title=f"Booking {booking.booking_reference}",
        )
        document.build(story)

        return pdf_buffer.getvalue()

    except Exception:
        await db.rollback()
        raise
