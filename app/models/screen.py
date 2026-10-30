
from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin


class Screen(TimestampMixin, Base):
    __tablename__ = "screens"
    __table_args__ = (
        UniqueConstraint("venue_id", "name", name="uq_screen_venue_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)

    venue_id: Mapped[int] = mapped_column(
        ForeignKey("venues.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(String(100), nullable=False)

    total_rows: Mapped[int] = mapped_column(
        Integer, nullable=False
    )

    seats_per_row: Mapped[int] = mapped_column(
        Integer, nullable=False
    )

    total_seats: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )

    venue = relationship("Venue", back_populates="screens")

    seats = relationship(
        "Seat",
        back_populates="screen",
        cascade="all, delete-orphan",
    )

    shows = relationship("Show", back_populates="screen")
