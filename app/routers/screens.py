
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_roles
from app.database import get_db
from app.models.screen import Screen
from app.models.venue import Venue
from app.schemas.screen import ScreenCreate, ScreenResponse

router = APIRouter(tags=["Screens"])


@router.post(
    "/venues/{venue_id}/screens",
    response_model=ScreenResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_screen(
    venue_id: int,
    payload: ScreenCreate,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("Admin")),
):
    venue = await db.get(Venue, venue_id)

    if venue is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Venue not found.",
        )

    if not venue.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot add a screen to an inactive venue.",
        )

    screen = Screen(
        venue_id=venue_id,
        name=payload.name,
        total_rows=payload.total_rows,
        seats_per_row=payload.seats_per_row,
        total_seats=0,
    )
    db.add(screen)

    try:
        await db.commit()
        await db.refresh(screen)
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A screen with this name already exists at this venue.",
        )

    return screen
