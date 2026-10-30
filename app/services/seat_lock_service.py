
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.booked_seat import BookedSeat
from app.models.booking import Booking
from app.models.event import Event
from app.models.seat import Seat
from app.models.show import Show


LOCK_TTL_SECONDS = 600
MAX_SEATS_PER_BOOKING = 10

CONFIRMED_BOOKING_STATUSES = {
    "confirmed",
    "paid",
    "completed",
    "booked",
}

ACTIVE_SEAT_STATUSES = {
    "reserved",
    "confirmed",
    "paid",
    "booked",
    "checked_in",
}


def seat_lock_key(show_id: int, seat_id: int) -> str:
    return f"lock:show:{show_id}:seat:{seat_id}"


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _as_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _seat_sort_key(seat: Seat):
    number = seat.seat_number
    if number.isdigit():
        return seat.row_label, 0, int(number), seat.id
    return seat.row_label, 1, number, seat.id


async def get_show_or_404(
    db: AsyncSession,
    show_id: int,
) -> Show:
    result = await db.execute(
        select(Show).where(Show.id == show_id)
    )
    show = result.scalar_one_or_none()

    if show is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Show not found.",
        )

    if (show.status or "").lower() != "scheduled":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Seats are unavailable because this show is not scheduled.",
        )

    if (
        show.booking_closes_at is not None
        and _as_aware(show.booking_closes_at) <= _now_utc()
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Booking is closed for this show.",
        )

    event_result = await db.execute(
        select(Event.status).where(Event.id == show.event_id)
    )
    event_status = event_result.scalar_one_or_none()

    if event_status is None or event_status.lower() != "published":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This event is not available for booking.",
        )

    return show


async def _get_booked_seat_ids(
    db: AsyncSession,
    show_id: int,
) -> set[int]:
    now = _now_utc()

    result = await db.execute(
        select(BookedSeat.seat_id)
        .join(Booking, Booking.id == BookedSeat.booking_id)
        .where(
            Booking.show_id == show_id,
            (
                func.lower(Booking.status).in_(
                    CONFIRMED_BOOKING_STATUSES
                )
                | (
                    (func.lower(Booking.status) == "pending")
                    & Booking.expires_at.is_not(None)
                    & (Booking.expires_at > now)
                )
            ),
            func.lower(BookedSeat.status).in_(
                ACTIVE_SEAT_STATUSES
            ),
        )
    )

    return set(result.scalars().all())


async def _get_active_seats(
    db: AsyncSession,
    show: Show,
) -> list[Seat]:
    result = await db.execute(
        select(Seat).where(
            Seat.screen_id == show.screen_id,
            Seat.is_active.is_(True),
        )
    )
    seats = list(result.scalars().all())
    seats.sort(key=_seat_sort_key)
    return seats


async def _get_lock_values(
    redis: Redis,
    show_id: int,
    seats: list[Seat],
) -> dict[int, str | None]:
    keys = [
        seat_lock_key(show_id, seat.id)
        for seat in seats
    ]
    values = await redis.mget(keys) if keys else []

    return {
        seat.id: value
        for seat, value in zip(seats, values)
    }



def _find_new_single_empty_seats(
    seats: list[Seat],
    unavailable_before: set[int],
    unavailable_after: set[int],
) -> list[int]:
    """
    Return seats that become the only available seat in a
    contiguous row segment because of the proposed selection.
    """
    rows: dict[str, list[Seat]] = {}

    for seat in seats:
        rows.setdefault(seat.row_label, []).append(seat)

    def find_single_seats(
        row_seats: list[Seat],
        unavailable: set[int],
    ) -> set[int]:
        single_seats: set[int] = set()
        available_run: list[Seat] = []

        for seat in row_seats:
            if seat.id not in unavailable:
                available_run.append(seat)
            else:
                if len(available_run) == 1:
                    single_seats.add(available_run[0].id)
                available_run = []

        # Check the final available segment in the row.
        if len(available_run) == 1:
            single_seats.add(available_run[0].id)

        return single_seats

    violations: set[int] = set()

    for row_seats in rows.values():
        row_seats.sort(key=_seat_sort_key)

        singles_before = find_single_seats(
            row_seats,
            unavailable_before,
        )
        singles_after = find_single_seats(
            row_seats,
            unavailable_after,
        )

        violations.update(singles_after - singles_before)

    return sorted(violations)


async def get_seat_map(
    db: AsyncSession,
    show: Show,
    redis: Redis,
    customer_id: int | None = None,
) -> list[dict]:
    seats = await _get_active_seats(db, show)
    booked_ids = await _get_booked_seat_ids(db, show.id)
    lock_values = await _get_lock_values(redis, show.id, seats)

    result = []

    for seat in seats:
        owner = lock_values.get(seat.id)
        is_booked = seat.id in booked_ids
        is_locked = owner is not None

        result.append(
            {
                "seat_id": seat.id,
                "row": seat.row_label,
                "seat_number": seat.seat_number,
                "category": seat.category,
                "available": not is_booked and not is_locked,
                "booked": is_booked,
                "locked": is_locked,
                "locked_by_current_user": (
                    customer_id is not None
                    and owner == str(customer_id)
                ),
            }
        )

    return result


async def lock_seats(
    db: AsyncSession,
    redis: Redis,
    show_id: int,
    seat_ids: list[int],
    customer_id: int,
) -> dict:
    if not seat_ids:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Select at least one seat.",
        )

    if len(seat_ids) > MAX_SEATS_PER_BOOKING:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"You can lock at most {MAX_SEATS_PER_BOOKING} seats per show.",
        )

    if len(set(seat_ids)) != len(seat_ids):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Duplicate seat IDs are not allowed.",
        )

    show = await get_show_or_404(db, show_id)

    seats = await _get_active_seats(db, show)
    requested_ids = set(seat_ids)
    seat_by_id = {seat.id: seat for seat in seats}

    if not requested_ids.issubset(seat_by_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "One or more seats do not exist, are inactive, "
                "or belong to another screen."
            ),
        )

    booked_ids = await _get_booked_seat_ids(db, show.id)
    already_booked = requested_ids.intersection(booked_ids)

    if already_booked:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "One or more seats are already booked.",
                "seat_ids": sorted(already_booked),
            },
        )

    lock_values = await _get_lock_values(redis, show.id, seats)

    conflicts = [
        seat_id
        for seat_id in requested_ids
        if lock_values.get(seat_id) is not None
        and lock_values[seat_id] != str(customer_id)
    ]

    if conflicts:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": (
                    "One or more seats are currently locked "
                    "by another customer."
                ),
                "seat_ids": sorted(conflicts),
            },
        )

    unavailable_before = {
        seat_id
        for seat_id in seat_by_id
        if seat_id in booked_ids
        or lock_values.get(seat_id) is not None
    }
    unavailable_after = unavailable_before | requested_ids

    single_seat_violations = _find_new_single_empty_seats(
        seats,
        unavailable_before,
        unavailable_after,
    )

    if single_seat_violations:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "message": (
                    "This selection would leave an isolated "
                    "single empty seat in a row."
                ),
                "seat_ids": single_seat_violations,
            },
        )

    keys = [
        seat_lock_key(show.id, seat_id)
        for seat_id in sorted(requested_ids)
    ]

    # Perform the full acquisition atomically. If any key belongs
    # to another customer, no new lock is created or renewed.
    atomic_lock_script = """
    local owner = ARGV[1]
    local ttl = tonumber(ARGV[2])

    for i, key in ipairs(KEYS) do
        local value = redis.call('GET', key)
        if value and value ~= owner then
            return i
        end
    end

    for i, key in ipairs(KEYS) do
        local value = redis.call('GET', key)
        if value == owner then
            redis.call('EXPIRE', key, ttl)
        else
            redis.call('SET', key, owner, 'EX', ttl, 'NX')
        end
    end

    return 0
    """

    try:
        conflict_index = await redis.eval(
            atomic_lock_script,
            len(keys),
            *keys,
            str(customer_id),
            LOCK_TTL_SECONDS,
        )
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Seat locking is temporarily unavailable. "
                "Please try again."
            ),
        )

    if conflict_index:
        conflicting_seat_id = sorted(requested_ids)[
            int(conflict_index) - 1
        ]
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": (
                    "One or more seats were locked by another "
                    "customer. No new locks were acquired."
                ),
                "seat_ids": [conflicting_seat_id],
            },
        )

    return {
        "show_id": show.id,
        "seat_ids": sorted(requested_ids),
        "expires_in_seconds": LOCK_TTL_SECONDS,
        "expires_at": (
            _now_utc() + timedelta(seconds=LOCK_TTL_SECONDS)
        ).isoformat(),
        "message": "Seats locked successfully.",
    }


async def release_seats(
    redis: Redis,
    show_id: int,
    seat_ids: list[int],
    customer_id: int,
) -> dict:
    if not seat_ids:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Provide at least one seat ID to release.",
        )

    if len(set(seat_ids)) != len(seat_ids):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Duplicate seat IDs are not allowed.",
        )

    released = []
    not_owned = []

    # Owner check and deletion occur atomically.
    delete_if_owner_script = """
    if redis.call('GET', KEYS[1]) == ARGV[1] then
        return redis.call('DEL', KEYS[1])
    end
    return 0
    """

    try:
        for seat_id in seat_ids:
            key = seat_lock_key(show_id, seat_id)
            deleted = await redis.eval(
                delete_if_owner_script,
                1,
                key,
                str(customer_id),
            )

            if deleted:
                released.append(seat_id)
            else:
                not_owned.append(seat_id)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Seat-lock release is temporarily unavailable. "
                "Please try again."
            ),
        )

    return {
        "show_id": show_id,
        "released_seat_ids": released,
        "not_owned_or_expired_seat_ids": not_owned,
        "message": "Seat-lock release processed.",
    }
