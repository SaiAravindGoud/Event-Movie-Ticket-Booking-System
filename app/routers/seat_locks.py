
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_roles
from app.database import get_db
from app.models.user import User
from app.redis_client import get_redis
from app.services.rate_limit_service import enforce_rate_limit
from app.services.seat_lock_service import (
    get_seat_map,
    get_show_or_404,
    lock_seats,
    release_seats,
)


router = APIRouter(tags=["Seat Locking"])


class SeatLockRequest(BaseModel):
    seat_ids: list[int] = Field(min_length=1, max_length=10)


class SeatReleaseRequest(BaseModel):
    seat_ids: list[int] = Field(min_length=1, max_length=10)


@router.get("/shows/{show_id}/seats/availability")
async def show_seat_availability(
    show_id: int,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
    current_user: User = Depends(require_roles("Customer")),
):
    try:
        show = await get_show_or_404(db, show_id)

        seats = await get_seat_map(
            db,
            show,
            redis,
            customer_id=current_user.id,
        )

    except HTTPException:
        raise

    except Exception:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Seat availability is temporarily unavailable.",
        )

    return {
        "show_id": show_id,
        "seats": seats,
        "total_seats": len(seats),
        "available_seats": sum(
            1 for seat in seats if seat["available"]
        ),
        "booked_seats": sum(
            1 for seat in seats if seat["booked"]
        ),
        "locked_seats": sum(
            1 for seat in seats if seat["locked"]
        ),
    }


@router.post("/shows/{show_id}/seats/lock")
async def create_seat_lock(
    show_id: int,
    payload: SeatLockRequest,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
    current_user: User = Depends(require_roles("Customer")),
):
    await enforce_rate_limit(
        redis=redis,
        key=f"rate_limit:seat_lock:customer:{current_user.id}",
        limit=5,
        window_seconds=60,
    )

    return await lock_seats(
        db=db,
        redis=redis,
        show_id=show_id,
        seat_ids=payload.seat_ids,
        customer_id=current_user.id,
    )


@router.post("/shows/{show_id}/seats/release")
async def release_seat_lock(
    show_id: int,
    payload: SeatReleaseRequest,
    redis: Redis = Depends(get_redis),
    current_user: User = Depends(require_roles("Customer")),
):
    return await release_seats(
        redis=redis,
        show_id=show_id,
        seat_ids=payload.seat_ids,
        customer_id=current_user.id,
    )
