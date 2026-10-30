from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


EventCategory = Literal[
    "Movie",
    "Concert",
    "Comedy",
    "Sports",
    "Workshop",
]

AgeRating = Literal["U", "UA", "A"]
EventStatus = Literal["Draft", "Published", "Cancelled"]


class EventCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = None
    category: EventCategory
    language: str = Field(min_length=1, max_length=50)
    duration_minutes: int = Field(ge=30, le=300)
    age_rating: AgeRating
    poster_url: str | None = Field(default=None, max_length=500)
    release_date: datetime | None = None


class EventUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    category: EventCategory | None = None
    language: str | None = Field(default=None, min_length=1, max_length=50)
    duration_minutes: int | None = Field(
        default=None,
        ge=30,
        le=300,
    )
    age_rating: AgeRating | None = None
    poster_url: str | None = Field(default=None, max_length=500)
    release_date: datetime | None = None
    status: EventStatus | None = None


class EventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    organizer_id: int
    title: str
    description: str | None
    category: str
    event_type: str
    duration_minutes: int
    age_rating: str
    language: str | None
    poster_url: str | None
    status: str
    release_date: datetime | None