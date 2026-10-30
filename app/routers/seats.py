
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_roles
from app.database import get_db
from app.models.screen import Screen
from app.models.seat import Seat
from app.schemas.seat import (
    SeatBulkCreate,
    SeatLayoutResponse,
    SeatResponse,
    SeatStatusUpdate,
)

router = APIRouter(tags=["Seats"])


@router.post(
    "/screens/{screen_id}/seats/bulk",
    response_model=list[SeatResponse],
    status_code=status.HTTP_201_CREATED,
)
async def create_seats_bulk(
    screen_id: int,
    payload: SeatBulkCreate,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("Admin")),
):
    screen = await db.get(Screen, screen_id)

    if screen is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Screen not found.",
        )

    rows = [row.strip().upper() for row in payload.rows]

    if any(not row or len(row) > 5 for row in rows):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Each row label must contain between 1 and 5 characters.",
        )

    if len(set(rows)) != len(rows):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Row labels must be unique.",
        )

    if len(rows) > screen.total_rows:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"This screen supports at most {screen.total_rows} rows.",
        )

    if payload.seats_per_row > screen.seats_per_row:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"This screen supports at most "
                f"{screen.seats_per_row} seats per row."
            ),
        )

    existing_result = await db.execute(
        select(Seat.id).where(Seat.screen_id == screen_id).limit(1)
    )
    if existing_result.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Seats have already been created for this screen.",
        )

    seats = [
        Seat(
            screen_id=screen_id,
            row_label=row,
            seat_number=str(number),
            category=payload.category,
            seat_type=payload.category,
            is_active=True,
        )
        for row in rows
        for number in range(1, payload.seats_per_row + 1)
    ]

    db.add_all(seats)
    screen.total_seats = len(seats)

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Seat creation conflicted with existing seat records.",
        )

    for seat in seats:
        await db.refresh(seat)

    return seats


@router.get(
    "/screens/{screen_id}/layout",
    response_model=SeatLayoutResponse,
)
async def get_screen_layout(
    screen_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("Admin")),
):
    screen = await db.get(Screen, screen_id)

    if screen is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Screen not found.",
        )

    result = await db.execute(
        select(Seat)
        .where(Seat.screen_id == screen_id)
        .order_by(Seat.row_label, Seat.id)
    )
    seats = result.scalars().all()

    grouped_rows: dict[str, list[SeatResponse]] = {}

    for seat in seats:
        grouped_rows.setdefault(seat.row_label, []).append(
            SeatResponse.model_validate(seat)
        )

    for row_seats in grouped_rows.values():
        row_seats.sort(
            key=lambda seat: (
                0,
                int(seat.seat_number),
            )
            if seat.seat_number.isdigit()
            else (1, seat.seat_number)
        )

    return SeatLayoutResponse(
        screen_id=screen_id,
        rows=grouped_rows,
    )


@router.put("/seats/{seat_id}/status", response_model=SeatResponse)
async def update_seat_status(
    seat_id: int,
    payload: SeatStatusUpdate,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("Admin")),
):
    seat = await db.get(Seat, seat_id)

    if seat is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Seat not found.",
        )

    seat.is_active = payload.is_active
    await db.commit()
    await db.refresh(seat)

    return seat
