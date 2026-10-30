from datetime import date, datetime, time
from decimal import Decimal
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.database import get_db
from app.models.event import Event
from app.models.screen import Screen
from app.models.show import Show
from app.models.user import User
from app.models.venue import Venue
from app.redis_client import redis_client
from app.schemas.event import EventCreate, EventResponse, EventUpdate


router = APIRouter(prefix="/events", tags=["Events"])

logger = logging.getLogger(__name__)

EVENT_CACHE_TTL = 300


def ensure_event_manager(user: User, event: Event | None = None) -> None:
    """Allow Admins or the event's owning Organizer to manage an event."""

    if user.role == "Admin":
        return

    if user.role != "Organizer":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only Organizers and Admins can manage events.",
        )

    if not user.organizer_approved:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your Organizer account has not been approved.",
        )

    if event is not None and event.organizer_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can manage only your own events.",
        )


async def get_cached_response(cache_key: str):
    """Return cached JSON data, or None if unavailable."""

    try:
        cached_data = await redis_client.get(cache_key)

        if cached_data:
            return json.loads(cached_data)

    except Exception:
        logger.exception("Redis cache read failed for key %s", cache_key)

    return None


async def set_cached_response(cache_key: str, data) -> None:
    """Store JSON data in Redis for five minutes."""

    try:
        await redis_client.set(
            cache_key,
            json.dumps(data),
            ex=EVENT_CACHE_TTL,
        )
    except Exception:
        logger.exception("Redis cache write failed for key %s", cache_key)


async def invalidate_event_cache() -> None:
    """Remove event list and detail cache entries after changes."""

    try:
        keys = []

        async for key in redis_client.scan_iter(match="events:*"):
            keys.append(key)

        if keys:
            await redis_client.delete(*keys)

        logger.info("Event cache invalidated.")

    except Exception:
        logger.exception("Redis event cache invalidation failed.")


@router.post(
    "",
    response_model=EventResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_event(
    data: EventCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a Draft event for an approved Organizer or Admin."""

    if current_user.role == "Organizer":
        if not current_user.organizer_approved:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Your Organizer account has not been approved.",
            )
    elif current_user.role != "Admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only approved Organizers and Admins can create events.",
        )

    event = Event(
        organizer_id=current_user.id,
        title=data.title.strip(),
        description=data.description,
        category=data.category,
        event_type=data.category,
        duration_minutes=data.duration_minutes,
        age_rating=data.age_rating,
        language=data.language,
        poster_url=data.poster_url,
        release_date=data.release_date,
        status="Draft",
    )

    db.add(event)
    await db.commit()
    await db.refresh(event)

    await invalidate_event_cache()

    return event


@router.get("", response_model=list[EventResponse])
async def list_events(
    title: str | None = Query(default=None, min_length=1, max_length=200),
    city: str | None = Query(default=None, min_length=1, max_length=100),
    category: str | None = Query(default=None, max_length=50),
    language: str | None = Query(default=None, max_length=50),
    from_date: date | None = Query(default=None),
    to_date: date | None = Query(default=None),
    min_price: Decimal | None = Query(default=None, ge=0),
    max_price: Decimal | None = Query(default=None, ge=0),
    sort_by: str | None = Query(
        default=None,
        pattern="^(show_date|price|title)$",
    ),
    sort_order: str = Query(
        default="asc",
        pattern="^(asc|desc)$",
    ),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List events with filters, sorting, pagination, and Redis caching."""

    if current_user.role not in ("Customer", "Organizer", "Admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to view events.",
        )

    if from_date and to_date and from_date > to_date:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="from_date cannot be later than to_date.",
        )

    if (
        min_price is not None
        and max_price is not None
        and min_price > max_price
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="min_price cannot be greater than max_price.",
        )

    # Include every query parameter in the cache key.
    cache_key = (
        f"events:list:{current_user.role}:{current_user.id}:"
        f"title={title or ''}:"
        f"city={city or ''}:"
        f"category={category or ''}:"
        f"language={language or ''}:"
        f"from={from_date.isoformat() if from_date else ''}:"
        f"to={to_date.isoformat() if to_date else ''}:"
        f"min_price={str(min_price) if min_price is not None else ''}:"
        f"max_price={str(max_price) if max_price is not None else ''}:"
        f"sort_by={sort_by or 'default'}:"
        f"sort_order={sort_order}:"
        f"skip={skip}:limit={limit}"
    )

    cached_data = await get_cached_response(cache_key)

    if cached_data is not None:
        logger.info("Event list cache hit: %s", cache_key)
        return cached_data

    statement = select(Event)

    if current_user.role == "Customer":
        statement = statement.where(Event.status == "Published")

    elif current_user.role == "Organizer":
        statement = statement.where(
            Event.organizer_id == current_user.id
        )

    if title:
        statement = statement.where(
            Event.title.ilike(f"%{title.strip()}%")
        )

    if category:
        statement = statement.where(Event.category == category)

    if language:
        statement = statement.where(Event.language == language)

    # Filter by city through Show -> Screen -> Venue.
    if city:
        city_match = (
            select(Show.id)
            .join(Screen, Screen.id == Show.screen_id)
            .join(Venue, Venue.id == Screen.venue_id)
            .where(
                Show.event_id == Event.id,
                Venue.city.ilike(f"%{city.strip()}%"),
            )
            .exists()
        )
        statement = statement.where(city_match)

    # Filter by show date.
    if from_date:
        start_datetime = datetime.combine(from_date, time.min)

        date_match = (
            select(Show.id)
            .where(
                Show.event_id == Event.id,
                Show.start_time >= start_datetime,
            )
            .exists()
        )
        statement = statement.where(date_match)

    if to_date:
        end_datetime = datetime.combine(to_date, time.max)

        date_match = (
            select(Show.id)
            .where(
                Show.event_id == Event.id,
                Show.start_time <= end_datetime,
            )
            .exists()
        )
        statement = statement.where(date_match)

    # Price filters use Show.base_price.
    if min_price is not None or max_price is not None:
        price_conditions = [Show.event_id == Event.id]

        if min_price is not None:
            price_conditions.append(Show.base_price >= min_price)

        if max_price is not None:
            price_conditions.append(Show.base_price <= max_price)

        price_match = (
            select(Show.id)
            .where(*price_conditions)
            .exists()
        )
        statement = statement.where(price_match)

    # Correlated values allow sorting without duplicating event rows.
    if sort_by == "show_date":
        sort_expression = (
            select(func.min(Show.start_time))
            .where(Show.event_id == Event.id)
            .scalar_subquery()
        )

    elif sort_by == "price":
        sort_expression = (
            select(func.min(Show.base_price))
            .where(Show.event_id == Event.id)
            .scalar_subquery()
        )

    elif sort_by == "title":
        sort_expression = Event.title

    else:
        sort_expression = None

    if sort_expression is not None:
        if sort_order == "desc":
            statement = statement.order_by(
                sort_expression.desc(),
                Event.id.desc(),
            )
        else:
            statement = statement.order_by(
                sort_expression.asc().nulls_last(),
                Event.id.asc(),
            )
    else:
        statement = statement.order_by(Event.id.desc())

    statement = statement.offset(skip).limit(limit)

    result = await db.execute(statement)
    events = list(result.scalars().all())

    response_data = [
        EventResponse.model_validate(event).model_dump(mode="json")
        for event in events
    ]

    await set_cached_response(cache_key, response_data)

    return response_data


@router.get("/{event_id}", response_model=EventResponse)
async def get_event(
    event_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get event details with role-specific five-minute caching."""

    if current_user.role not in ("Customer", "Organizer", "Admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to view events.",
        )

    cache_key = (
        f"events:detail:{current_user.role}:"
        f"{current_user.id}:{event_id}"
    )

    cached_data = await get_cached_response(cache_key)

    if cached_data is not None:
        logger.info("Event detail cache hit: %s", cache_key)

        event_data = EventResponse.model_validate(cached_data)

        if current_user.role == "Customer":
            if event_data.status != "Published":
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Event not found.",
                )

        if current_user.role == "Organizer":
            if event_data.organizer_id != current_user.id:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Event not found.",
                )

        return cached_data

    event = await db.get(Event, event_id)

    if event is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Event not found.",
        )

    if current_user.role == "Organizer":
        if event.organizer_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You can view only your own events.",
            )

    elif current_user.role == "Customer":
        if event.status != "Published":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Event not found.",
            )

    response_data = (
        EventResponse.model_validate(event).model_dump(mode="json")
    )

    await set_cached_response(cache_key, response_data)

    return response_data


@router.put("/{event_id}", response_model=EventResponse)
async def update_event(
    event_id: int,
    data: EventUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update an event and invalidate cached event data."""

    event = await db.get(Event, event_id)

    if event is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Event not found.",
        )

    ensure_event_manager(current_user, event)

    changes = data.model_dump(exclude_unset=True)

    if "category" in changes:
        changes["event_type"] = changes["category"]

    for field, value in changes.items():
        setattr(event, field, value)

    await db.commit()
    await db.refresh(event)

    await invalidate_event_cache()

    return event