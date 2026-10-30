from pydantic import BaseModel, Field, EmailStr, field_validator
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_roles
from app.auth.security import hash_password
from app.database import get_db
from app.models.user import User
from app.models.venue import Venue
from app.schemas.auth import UserResponse


router = APIRouter(prefix="/admin", tags=["Administration"])


class StaffCreateRequest(BaseModel):
    full_name: str = Field(min_length=2, max_length=100)
    email: EmailStr
    password: str = Field(min_length=8, max_length=72)

    @field_validator("full_name")
    @classmethod
    def validate_full_name(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 2:
            raise ValueError("Full name must contain at least 2 characters.")
        return value


class StaffVenueAssignmentRequest(BaseModel):
    venue_id: int = Field(gt=0)


@router.post(
    "/staff",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_staff(
    payload: StaffCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("Admin")),
):
    """Create a Staff account. Admin access is required."""

    email = str(payload.email).lower()

    result = await db.execute(
        select(User).where(User.email == email)
    )
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists.",
        )

    staff = User(
        full_name=payload.full_name.strip(),
        email=email,
        hashed_password=hash_password(payload.password),
        role="Staff",
        is_active=True,
        organizer_approved=False,
    )

    db.add(staff)

    try:
        await db.commit()
        await db.refresh(staff)
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists.",
        )

    return staff


@router.put(
    "/organizers/{user_id}/approve",
    response_model=UserResponse,
)
async def approve_organizer(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("Admin")),
):
    """Approve an Organizer account. Admin access is required."""

    organizer = await db.get(User, user_id)

    if organizer is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found.",
        )

    if organizer.role != "Organizer":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This user is not an Organizer.",
        )

    if not organizer.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="An inactive Organizer cannot be approved.",
        )

    organizer.organizer_approved = True
    await db.commit()
    await db.refresh(organizer)

    return organizer


@router.put("/staff/{user_id}/venue")
async def assign_staff_venue(
    user_id: int,
    payload: StaffVenueAssignmentRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("Admin")),
):
    """Assign an existing Staff account to an active venue."""

    staff = await db.get(User, user_id)

    if staff is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found.",
        )

    if staff.role != "Staff":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only users with the Staff role can be assigned to a venue.",
        )

    if not staff.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="An inactive staff account cannot be assigned.",
        )

    venue = await db.get(Venue, payload.venue_id)

    if venue is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Venue not found.",
        )

    if not venue.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Staff cannot be assigned to an inactive venue.",
        )

    staff.venue_id = venue.id

    await db.commit()
    await db.refresh(staff)

    return {
        "message": "Staff venue assignment updated successfully.",
        "staff_id": staff.id,
        "staff_name": staff.full_name,
        "staff_email": staff.email,
        "venue_id": venue.id,
        "venue_name": venue.name,
    }
