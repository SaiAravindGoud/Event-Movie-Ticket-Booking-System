
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin


class Ticket(TimestampMixin, Base):
    __tablename__ = "tickets"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    booking_id: Mapped[int] = mapped_column(
        ForeignKey("bookings.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    booked_seat_id: Mapped[int] = mapped_column(
        ForeignKey("booked_seats.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
        index=True,
    )
    ticket_code: Mapped[str] = mapped_column(
        String(100), unique=True, nullable=False, index=True
    )
    qr_signature: Mapped[str] = mapped_column(
        String(255), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="Valid", index=True
    )
    scanned_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    scanned_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    booking = relationship("Booking", back_populates="tickets")
