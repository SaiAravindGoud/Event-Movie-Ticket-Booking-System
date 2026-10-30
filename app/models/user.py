from sqlalchemy import Boolean, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)

    full_name: Mapped[str] = mapped_column(
        String(120), nullable=False
    )

    email: Mapped[str] = mapped_column(
        String(255), unique=True, index=True, nullable=False
    )

    phone_number: Mapped[str | None] = mapped_column(
        String(10), nullable=True
    )

    hashed_password: Mapped[str] = mapped_column(
        String(255), nullable=False
    )

    role: Mapped[str] = mapped_column(
        String(20), nullable=False, default="Customer"
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )

    organizer_approved: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )

    venue_id: Mapped[int | None] = mapped_column(
        ForeignKey("venues.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    bookings = relationship("Booking", back_populates="customer")

    organized_events = relationship(
        "Event", back_populates="organizer"
    )