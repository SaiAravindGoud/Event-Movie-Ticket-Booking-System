
from sqlalchemy import (
    Boolean,
    ForeignKey,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin


class Seat(TimestampMixin, Base):
    __tablename__ = "seats"
    __table_args__ = (
        UniqueConstraint(
            "screen_id",
            "row_label",
            "seat_number",
            name="uq_seat_screen_row_number",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)

    screen_id: Mapped[int] = mapped_column(
        ForeignKey("screens.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    row_label: Mapped[str] = mapped_column(
        String(5),
        nullable=False,
        default="A",
        server_default=text("'A'"),
    )

    seat_number: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    category: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="Silver",
        server_default=text("'Silver'"),
    )

    # Retained for compatibility with the original model.
    seat_type: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="Regular",
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )

    screen = relationship("Screen", back_populates="seats")
