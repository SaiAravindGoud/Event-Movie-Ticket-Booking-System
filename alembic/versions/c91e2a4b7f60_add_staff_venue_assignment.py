
"""Add optional venue assignment to users.

Revision ID: c91e2a4b7f60
Revises: bdf67170e20b
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c91e2a4b7f60"
down_revision: Union[str, Sequence[str], None] = "bdf67170e20b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("venue_id", sa.Integer(), nullable=True),
    )

    op.create_index(
        "ix_users_venue_id",
        "users",
        ["venue_id"],
        unique=False,
    )

    op.create_foreign_key(
        "fk_users_venue_id_venues",
        "users",
        "venues",
        ["venue_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_users_venue_id_venues",
        "users",
        type_="foreignkey",
    )

    op.drop_index("ix_users_venue_id", table_name="users")

    op.drop_column("users", "venue_id")
