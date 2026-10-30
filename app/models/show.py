
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin


class Show(TimestampMixin, Base):
    __tablename__ = "shows"
    __table_args__ = (
        UniqueConstraint(
            "screen_id",
            "start_time",
            name="uq_show_screen_start_time",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)

    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    screen_id: Mapped[int] = mapped_column(
        ForeignKey("screens.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    start_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )

    end_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    base_price: Mapped[Decimal] = mapped_column(
        Numeric(10, 2),
        nullable=False,
    )

    silver_price: Mapped[Decimal] = mapped_column(
        Numeric(10, 2),
        nullable=False,
        default=0,
    )

    gold_price: Mapped[Decimal] = mapped_column(
        Numeric(10, 2),
        nullable=False,
        default=0,
    )

    recliner_price: Mapped[Decimal] = mapped_column(
        Numeric(10, 2),
        nullable=False,
        default=0,
    )

    booking_closes_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="Scheduled",
        index=True,
    )

    event = relationship("Event", back_populates="shows")
    screen = relationship("Screen", back_populates="shows")
    bookings = relationship("Booking", back_populates="show")

    category_prices = relationship(
        "ShowCategoryPrice",
        back_populates="show",
        cascade="all, delete-orphan",
    )
