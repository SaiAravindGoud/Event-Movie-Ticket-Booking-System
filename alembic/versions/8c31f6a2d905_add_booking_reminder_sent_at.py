
"""Add booking reminder sent timestamp.

Revision ID: 8c31f6a2d905
Revises: 7a4d9c2e1f60
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "8c31f6a2d905"
down_revision: Union[str, Sequence[str], None] = "7a4d9c2e1f60"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "bookings",
        sa.Column(
            "reminder_sent_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("bookings", "reminder_sent_at")
