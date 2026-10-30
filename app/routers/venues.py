from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_roles
from app.database import get_db
from app.models.venue import Venue
from app.schemas.venue import VenueCreate, VenueResponse, VenueUpdate

router = APIRouter(prefix="/venues", tags=["Venues"])


@router.post(
    "",
    response_model=VenueResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_venue(
    payload: VenueCreate,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("Admin")),
):
    venue = Venue(**payload.model_dump())
    db.add(venue)

    try:
        await db.commit()
        await db.refresh(venue)
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Could not create the venue because of a data conflict.",
        )

    return venue


@router.get("", response_model=list[VenueResponse])
async def list_venues(
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    city: str | None = Query(default=None, min_length=1, max_length=100),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("Admin")),
):
    """List venues with optional city filtering and pagination."""

    statement = select(Venue)

    if city:
        statement = statement.where(
            Venue.city.ilike(f"%{city.strip()}%")
        )

    statement = (
        statement
        .order_by(Venue.id.asc())
        .offset(skip)
        .limit(limit)
    )

    result = await db.execute(statement)
    return result.scalars().all()


@router.put("/{venue_id}", response_model=VenueResponse)
async def update_venue(
    venue_id: int,
    payload: VenueUpdate,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("Admin")),
):
    venue = await db.get(Venue, venue_id)

    if venue is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Venue not found.",
        )

    updates = payload.model_dump(exclude_unset=True)

    for field, value in updates.items():
        if value is not None:
            setattr(venue, field, value)

    try:
        await db.commit()
        await db.refresh(venue)
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Could not update the venue because of a data conflict.",
        )

    return venue