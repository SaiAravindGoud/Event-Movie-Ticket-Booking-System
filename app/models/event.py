
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin


class Event(TimestampMixin, Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)

    organizer_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    title: Mapped[str] = mapped_column(
        String(200), nullable=False, index=True
    )

    description: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )

    # Category describes the event's content or genre.
    category: Mapped[str] = mapped_column(
        String(50), nullable=False, default="Movie"
    )

    # Retained to avoid breaking existing code.
    event_type: Mapped[str] = mapped_column(
        String(30), nullable=False, default="Movie"
    )

    duration_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False
    )

    age_rating: Mapped[str] = mapped_column(
        String(20), nullable=False, default="U"
    )

    language: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )

    poster_url: Mapped[str | None] = mapped_column(
        String(500), nullable=True
    )

    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="Draft", index=True
    )

    release_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    organizer = relationship("User", back_populates="organized_events")
    shows = relationship("Show", back_populates="event")
