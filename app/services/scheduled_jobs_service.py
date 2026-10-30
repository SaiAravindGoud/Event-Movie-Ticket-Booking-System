
from datetime import datetime, timedelta

from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.models.booking import Booking
from app.models.event import Event
from app.models.payment import Payment
from app.models.show import Show
from app.models.user import User
from app.services.email_service import send_email
from app.services.ticket_service import generate_booking_ticket_pdf


async def run_show_reminder_job() -> None:
    """
    Email customers with PDF tickets when their show starts
    within the next three hours.

    Each booking receives a reminder only once.
    """

    now = datetime.now()
    reminder_deadline = now + timedelta(hours=3)

    async with AsyncSessionLocal() as db:
        try:
            paid_booking_exists = exists(
                select(Payment.id).where(
                    Payment.booking_id == Booking.id,
                    func.lower(Payment.status) == "paid",
                )
            )

            result = await db.execute(
                select(
                    Booking.id,
                    Booking.customer_id,
                    User.email,
                )
                .join(User, User.id == Booking.customer_id)
                .join(Show, Show.id == Booking.show_id)
                .where(
                    func.lower(Booking.status) == "confirmed",
                    Booking.reminder_sent_at.is_(None),
                    func.lower(Show.status) == "scheduled",
                    Show.start_time >= now,
                    Show.start_time <= reminder_deadline,
                    paid_booking_exists,
                )
                .order_by(Show.start_time.asc(), Booking.id.asc())
            )

            reminders = result.all()

        except Exception:
            await db.rollback()
            raise

    sent_count = 0

    for booking_id, customer_id, customer_email in reminders:
        try:
            # Generate the PDF using a separate database session.
            async with AsyncSessionLocal() as pdf_db:
                pdf_content = await generate_booking_ticket_pdf(
                    db=pdf_db,
                    booking_id=booking_id,
                    customer_id=customer_id,
                )

            email_sent = await send_email(
                recipient=customer_email,
                subject="Reminder: Your event/show is starting soon",
                body=(
                    f"This is a reminder for your booking #{booking_id}. "
                    "Your show starts within the next three hours. "
                    "Please arrive at the venue on time and present "
                    "your ticket QR code at entry. "
                    "Your PDF tickets are attached."
                ),
                attachment=pdf_content,
                attachment_filename=f"booking-{booking_id}-reminder.pdf",
            )

            if not email_sent:
                print(
                    f"Show reminder email was not sent for "
                    f"booking {booking_id}. It will be retried."
                )
                continue

            # Mark the reminder only after successful email delivery.
            async with AsyncSessionLocal() as update_db:
                booking_result = await update_db.execute(
                    select(Booking).where(
                        Booking.id == booking_id,
                        func.lower(Booking.status) == "confirmed",
                        Booking.reminder_sent_at.is_(None),
                    )
                )

                booking = booking_result.scalar_one_or_none()

                if booking is not None:
                    booking.reminder_sent_at = datetime.now()
                    await update_db.commit()
                    sent_count += 1

        except Exception as exc:
            print(
                f"Show reminder job failed for booking {booking_id}: "
                f"{type(exc).__name__}: {exc}"
            )

    print(
        f"Show reminder job completed: "
        f"{sent_count} reminder email(s) sent."
    )


async def run_show_completion_job() -> None:
    """
    Mark scheduled shows as Completed when their end time has passed.
    """

    now = datetime.now()

    async with AsyncSessionLocal() as db:
        try:
            result = await db.execute(
                select(Show)
                .where(
                    func.lower(Show.status) == "scheduled",
                    Show.end_time <= now,
                )
                .with_for_update()
            )

            shows = list(result.scalars().all())

            for show in shows:
                show.status = "Completed"

            if shows:
                await db.commit()
                print(
                    f"Show completion job: marked "
                    f"{len(shows)} show(s) as Completed."
                )

        except Exception:
            await db.rollback()
            raise


async def run_event_completion_job() -> None:
    """
    Mark events Completed when they have at least one show
    and every show belonging to them is Completed.
    """

    async with AsyncSessionLocal() as db:
        try:
            event_has_shows = exists(
                select(Show.id).where(
                    Show.event_id == Event.id
                )
            )

            event_has_incomplete_shows = exists(
                select(Show.id).where(
                    Show.event_id == Event.id,
                    func.lower(Show.status) != "completed",
                )
            )

            result = await db.execute(
                select(Event)
                .where(
                    event_has_shows,
                    ~event_has_incomplete_shows,
                    func.lower(Event.status).notin_(
                        ["cancelled", "canceled"]
                    ),
                )
                .with_for_update()
            )

            events = list(result.scalars().all())

            updated_count = 0

            for event in events:
                if (event.status or "").lower() != "completed":
                    event.status = "Completed"
                    updated_count += 1

            if updated_count:
                await db.commit()

            print(
                f"Event completion job: marked "
                f"{updated_count} event(s) as Completed."
            )

        except Exception:
            await db.rollback()
            raise
