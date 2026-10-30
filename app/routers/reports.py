
from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.database import get_db
from app.models.booking import Booking
from app.models.booked_seat import BookedSeat
from app.models.event import Event
from app.models.payment import Payment
from app.models.refund import Refund
from app.models.screen import Screen
from app.models.seat import Seat
from app.models.show import Show
from app.models.ticket import Ticket
from app.models.user import User

router = APIRouter(prefix="/reports", tags=["Reports"])


def require_report_role(
    user: User,
    allowed_roles: tuple[str, ...],
) -> None:
    if user.role not in allowed_roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to access this report.",
        )


def require_approved_organizer(user: User) -> None:
    if user.role == "Organizer" and not user.organizer_approved:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your organizer account is not approved.",
        )


def validate_date_range(
    start_date: date | None,
    end_date: date | None,
) -> None:
    if start_date and end_date and start_date > end_date:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="start_date cannot be later than end_date.",
        )


def date_conditions(
    column,
    start_date: date | None,
    end_date: date | None,
) -> list:
    conditions = []

    if start_date:
        conditions.append(
            column >= datetime.combine(start_date, time.min)
        )

    if end_date:
        conditions.append(
            column < datetime.combine(
                end_date + timedelta(days=1),
                time.min,
            )
        )

    return conditions


def booking_sales_subqueries(
    start_date: date | None,
    end_date: date | None,
):
    """Build separate aggregates to avoid multiplying payment rows."""

    sold_query = (
        select(
            Booking.show_id.label("show_id"),
            func.count(BookedSeat.id).label("tickets_sold"),
        )
        .join(
            BookedSeat,
            BookedSeat.booking_id == Booking.id,
        )
        .join(Show, Show.id == Booking.show_id)
        .where(
            func.lower(Booking.status).in_(
                ["confirmed", "paid", "completed", "booked"]
            ),
            func.lower(BookedSeat.status) == "confirmed",
            *date_conditions(Show.start_time, start_date, end_date),
        )
        .group_by(Booking.show_id)
        .subquery()
    )

    paid_per_booking = (
        select(
            Payment.booking_id.label("booking_id"),
            func.sum(Payment.amount).label("paid_amount"),
        )
        .where(
            func.lower(Payment.status) == "paid",
            *date_conditions(Payment.paid_at, start_date, end_date),
        )
        .group_by(Payment.booking_id)
        .subquery()
    )

    revenue_query = (
        select(
            Booking.show_id.label("show_id"),
            func.sum(paid_per_booking.c.paid_amount).label(
                "revenue"
            ),
        )
        .join(
            paid_per_booking,
            paid_per_booking.c.booking_id == Booking.id,
        )
        .join(Show, Show.id == Booking.show_id)
        .where(
            *date_conditions(Show.start_time, start_date, end_date)
        )
        .group_by(Booking.show_id)
        .subquery()
    )

    refund_per_booking = (
        select(
            Payment.booking_id.label("booking_id"),
            func.sum(Refund.amount).label("refund_amount"),
        )
        .join(Refund, Refund.payment_id == Payment.id)
        .where(
            func.lower(Refund.status) == "processed",
            *date_conditions(
                Refund.processed_at,
                start_date,
                end_date,
            ),
        )
        .group_by(Payment.booking_id)
        .subquery()
    )

    refund_query = (
        select(
            Booking.show_id.label("show_id"),
            func.sum(refund_per_booking.c.refund_amount).label(
                "refund_amount"
            ),
        )
        .join(
            refund_per_booking,
            refund_per_booking.c.booking_id == Booking.id,
        )
        .join(Show, Show.id == Booking.show_id)
        .where(
            *date_conditions(Show.start_time, start_date, end_date)
        )
        .group_by(Booking.show_id)
        .subquery()
    )

    return sold_query, revenue_query, refund_query


@router.get("/admin-dashboard")
async def admin_dashboard(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return today's booking, revenue, refund and entry statistics."""

    require_report_role(current_user, ("Admin",))

    today = date.today()
    start = datetime.combine(today, time.min)
    end = datetime.combine(today + timedelta(days=1), time.min)

    bookings_result = await db.execute(
        select(func.count(Booking.id)).where(
            Booking.created_at >= start,
            Booking.created_at < end,
        )
    )

    revenue_result = await db.execute(
        select(func.coalesce(func.sum(Payment.amount), 0)).where(
            func.lower(Payment.status) == "paid",
            Payment.paid_at >= start,
            Payment.paid_at < end,
        )
    )

    refunds_result = await db.execute(
        select(func.coalesce(func.sum(Refund.amount), 0)).where(
            func.lower(Refund.status) == "processed",
            Refund.processed_at >= start,
            Refund.processed_at < end,
        )
    )

    events_result = await db.execute(
        select(func.count(Event.id)).where(
            Event.status == "Published"
        )
    )

    scanned_result = await db.execute(
        select(func.count(Ticket.id)).where(
            Ticket.scanned_at >= start,
            Ticket.scanned_at < end,
        )
    )

    return {
        "date": today.isoformat(),
        "today_bookings": bookings_result.scalar_one(),
        "today_revenue": str(revenue_result.scalar_one()),
        "today_refunds": str(refunds_result.scalar_one()),
        "active_events": events_result.scalar_one(),
        "tickets_scanned_today": scanned_result.scalar_one(),
    }


@router.get("/event-sales")
async def event_sales_report(
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    event_id: int | None = Query(default=None, gt=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Show ticket sales, revenue and refunds for each show."""

    require_report_role(
        current_user,
        ("Admin", "Organizer"),
    )
    require_approved_organizer(current_user)
    validate_date_range(start_date, end_date)

    sold, revenue, refunds = booking_sales_subqueries(
        start_date,
        end_date,
    )

    statement = (
        select(
            Event.id.label("event_id"),
            Event.title.label("event_title"),
            Show.id.label("show_id"),
            Show.start_time,
            func.coalesce(sold.c.tickets_sold, 0).label(
                "tickets_sold"
            ),
            func.coalesce(revenue.c.revenue, 0).label("revenue"),
            func.coalesce(
                refunds.c.refund_amount,
                0,
            ).label("refund_amount"),
        )
        .join(Show, Show.event_id == Event.id)
        .outerjoin(sold, sold.c.show_id == Show.id)
        .outerjoin(revenue, revenue.c.show_id == Show.id)
        .outerjoin(refunds, refunds.c.show_id == Show.id)
        .where(
            *date_conditions(Show.start_time, start_date, end_date)
        )
        .order_by(Show.start_time.desc(), Show.id.desc())
    )

    if event_id is not None:
        statement = statement.where(Event.id == event_id)

    if current_user.role == "Organizer":
        statement = statement.where(
            Event.organizer_id == current_user.id
        )

    result = await db.execute(statement)
    rows = result.mappings().all()

    return {
        "start_date": (
            start_date.isoformat() if start_date else None
        ),
        "end_date": end_date.isoformat() if end_date else None,
        "count": len(rows),
        "sales": [
            {
                "event_id": row["event_id"],
                "event_title": row["event_title"],
                "show_id": row["show_id"],
                "start_time": row["start_time"],
                "tickets_sold": row["tickets_sold"],
                "revenue": str(row["revenue"]),
                "refund_amount": str(row["refund_amount"]),
            }
            for row in rows
        ],
    }


@router.get("/occupancy")
async def occupancy_report(
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    event_id: int | None = Query(default=None, gt=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Calculate occupancy by show and active seat category."""

    require_report_role(
        current_user,
        ("Admin", "Organizer"),
    )
    require_approved_organizer(current_user)
    validate_date_range(start_date, end_date)

    active_seats = (
        select(
            Seat.screen_id.label("screen_id"),
            Seat.category.label("category"),
            func.count(Seat.id).label("capacity"),
        )
        .where(Seat.is_active.is_(True))
        .group_by(Seat.screen_id, Seat.category)
        .subquery()
    )

    sold_seats = (
        select(
            Booking.show_id.label("show_id"),
            Seat.category.label("category"),
            func.count(BookedSeat.id).label("sold"),
        )
        .join(
            BookedSeat,
            BookedSeat.booking_id == Booking.id,
        )
        .join(Seat, Seat.id == BookedSeat.seat_id)
        .join(Show, Show.id == Booking.show_id)
        .where(
            func.lower(Booking.status).in_(
                ["confirmed", "paid", "completed", "booked"]
            ),
            func.lower(BookedSeat.status) == "confirmed",
            Seat.is_active.is_(True),
            *date_conditions(Show.start_time, start_date, end_date),
        )
        .group_by(Booking.show_id, Seat.category)
        .subquery()
    )

    statement = (
        select(
            Event.id.label("event_id"),
            Event.title.label("event_title"),
            Show.id.label("show_id"),
            Show.start_time,
            Screen.id.label("screen_id"),
            active_seats.c.category,
            active_seats.c.capacity,
            func.coalesce(sold_seats.c.sold, 0).label("occupied"),
        )
        .join(Show, Show.event_id == Event.id)
        .join(Screen, Screen.id == Show.screen_id)
        .join(
            active_seats,
            active_seats.c.screen_id == Screen.id,
        )
        .outerjoin(
            sold_seats,
            (sold_seats.c.show_id == Show.id)
            & (sold_seats.c.category == active_seats.c.category),
        )
        .where(
            *date_conditions(Show.start_time, start_date, end_date)
        )
        .order_by(Show.start_time.desc(), active_seats.c.category)
    )

    if event_id is not None:
        statement = statement.where(Event.id == event_id)

    if current_user.role == "Organizer":
        statement = statement.where(
            Event.organizer_id == current_user.id
        )

    result = await db.execute(statement)
    rows = result.mappings().all()

    occupancy = []

    for row in rows:
        capacity = row["capacity"]
        occupied = row["occupied"]

        percentage = (
            round((occupied / capacity) * 100, 2)
            if capacity
            else 0
        )

        occupancy.append(
            {
                "event_id": row["event_id"],
                "event_title": row["event_title"],
                "show_id": row["show_id"],
                "start_time": row["start_time"],
                "screen_id": row["screen_id"],
                "category": row["category"],
                "total_active_seats": capacity,
                "occupied_seats": occupied,
                "available_seats": max(capacity - occupied, 0),
                "occupancy_percentage": percentage,
            }
        )

    return {
        "count": len(occupancy),
        "occupancy": occupancy,
    }


@router.get("/top-events")
async def top_events_report(
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    limit: int = Query(default=10, ge=1, le=10),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the top events ranked by confirmed tickets sold."""

    require_report_role(current_user, ("Admin",))
    validate_date_range(start_date, end_date)

    sold, revenue, _ = booking_sales_subqueries(
        start_date,
        end_date,
    )

    sold_by_event = (
        select(
            Show.event_id.label("event_id"),
            func.sum(sold.c.tickets_sold).label("tickets_sold"),
        )
        .join(sold, sold.c.show_id == Show.id)
        .group_by(Show.event_id)
        .subquery()
    )

    revenue_by_event = (
        select(
            Show.event_id.label("event_id"),
            func.sum(revenue.c.revenue).label("revenue"),
        )
        .join(revenue, revenue.c.show_id == Show.id)
        .group_by(Show.event_id)
        .subquery()
    )

    statement = (
        select(
            Event.id.label("event_id"),
            Event.title.label("event_title"),
            func.coalesce(
                sold_by_event.c.tickets_sold,
                0,
            ).label("tickets_sold"),
            func.coalesce(
                revenue_by_event.c.revenue,
                0,
            ).label("revenue"),
        )
        .outerjoin(
            sold_by_event,
            sold_by_event.c.event_id == Event.id,
        )
        .outerjoin(
            revenue_by_event,
            revenue_by_event.c.event_id == Event.id,
        )
        .order_by(
            sold_by_event.c.tickets_sold.desc(),
            Event.id.asc(),
        )
        .limit(limit)
    )

    result = await db.execute(statement)
    rows = result.mappings().all()

    return {
        "start_date": (
            start_date.isoformat() if start_date else None
        ),
        "end_date": end_date.isoformat() if end_date else None,
        "count": len(rows),
        "top_events": [
            {
                "event_id": row["event_id"],
                "event_title": row["event_title"],
                "tickets_sold": row["tickets_sold"],
                "revenue": str(row["revenue"]),
            }
            for row in rows
        ],
    }


@router.get("/refunds")
async def refunds_report(
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Summarize processed refunds by reason."""

    require_report_role(current_user, ("Admin",))
    validate_date_range(start_date, end_date)

    reason_column = func.coalesce(
        Refund.reason,
        "Unspecified",
    ).label("reason")

    statement = (
        select(
            reason_column,
            func.count(Refund.id).label("refund_count"),
            func.coalesce(
                func.sum(Refund.amount),
                0,
            ).label("total_amount"),
        )
        .where(
            func.lower(Refund.status) == "processed",
            *date_conditions(
                Refund.processed_at,
                start_date,
                end_date,
            ),
        )
        .group_by(reason_column)
        .order_by(func.count(Refund.id).desc())
    )

    result = await db.execute(statement)
    rows = result.mappings().all()

    return {
        "start_date": (
            start_date.isoformat() if start_date else None
        ),
        "end_date": end_date.isoformat() if end_date else None,
        "count": len(rows),
        "refunds": [
            {
                "reason": row["reason"],
                "refund_count": row["refund_count"],
                "total_amount": str(row["total_amount"]),
            }
            for row in rows
        ],
    }
