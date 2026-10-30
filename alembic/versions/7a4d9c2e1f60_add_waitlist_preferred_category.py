
"""Add preferred seat category to waitlist entries.

Revision ID: 7a4d9c2e1f60
Revises: c91e2a4b7f60
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "7a4d9c2e1f60"
down_revision: Union[str, Sequence[str], None] = "c91e2a4b7f60"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "waitlist_entries",
        sa.Column(
            "preferred_category",
            sa.String(length=50),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("waitlist_entries", "preferred_category")
