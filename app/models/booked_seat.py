
from decimal import Decimal

from sqlalchemy import ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin


class BookedSeat(TimestampMixin, Base):
    __tablename__ = "booked_seats"
    __table_args__ = (
        UniqueConstraint(
            "booking_id",
            "seat_id",
            name="uq_booked_seat_booking_seat",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    booking_id: Mapped[int] = mapped_column(
        ForeignKey("bookings.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    seat_id: Mapped[int] = mapped_column(
        ForeignKey("seats.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    seat_price: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="Reserved"
    )

    booking = relationship("Booking", back_populates="booked_seats")
