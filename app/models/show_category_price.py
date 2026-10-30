
from decimal import Decimal

from sqlalchemy import (
    ForeignKey,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin


class ShowCategoryPrice(TimestampMixin, Base):
    __tablename__ = "show_category_prices"
    __table_args__ = (
        UniqueConstraint(
            "show_id",
            "category",
            name="uq_show_category_price",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)

    show_id: Mapped[int] = mapped_column(
        ForeignKey("shows.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    category: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )

    price: Mapped[Decimal] = mapped_column(
        Numeric(10, 2),
        nullable=False,
    )

    show = relationship("Show", back_populates="category_prices")
