
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin


class WaitlistEntry(TimestampMixin, Base):
    __tablename__ = "waitlist_entries"
    __table_args__ = (
        UniqueConstraint(
            "show_id",
            "customer_id",
            name="uq_waitlist_show_customer",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    show_id: Mapped[int] = mapped_column(
        ForeignKey("shows.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    requested_seats: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1
    )
    preferred_category: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="Waiting", index=True
    )
    notified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    show = relationship("Show")
    customer = relationship("User")
