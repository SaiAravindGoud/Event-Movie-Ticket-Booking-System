
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin


class PricingRule(TimestampMixin, Base):
    __tablename__ = "pricing_rules"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    show_id: Mapped[int] = mapped_column(
        ForeignKey("shows.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    adjustment_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="Percentage"
    )
    adjustment_value: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False
    )
    starts_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ends_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )

    show = relationship("Show")
