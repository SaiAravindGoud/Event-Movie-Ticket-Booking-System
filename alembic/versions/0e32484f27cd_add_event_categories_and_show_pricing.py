
"""add event categories and show pricing

Revision ID: 0e32484f27cd
Revises: 17407d4991fb
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0e32484f27cd"
down_revision: Union[str, Sequence[str], None] = "17407d4991fb"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add event fields with temporary defaults for existing records.
    op.add_column(
        "events",
        sa.Column(
            "category",
            sa.String(length=50),
            nullable=False,
            server_default="Movie",
        ),
    )
    op.add_column(
        "events",
        sa.Column(
            "age_rating",
            sa.String(length=20),
            nullable=False,
            server_default="U",
        ),
    )

    # Add show pricing fields with temporary defaults.
    op.add_column(
        "shows",
        sa.Column(
            "silver_price",
            sa.Numeric(precision=10, scale=2),
            nullable=False,
            server_default="0.00",
        ),
    )
    op.add_column(
        "shows",
        sa.Column(
            "gold_price",
            sa.Numeric(precision=10, scale=2),
            nullable=False,
            server_default="0.00",
        ),
    )
    op.add_column(
        "shows",
        sa.Column(
            "recliner_price",
            sa.Numeric(precision=10, scale=2),
            nullable=False,
            server_default="0.00",
        ),
    )
    op.add_column(
        "shows",
        sa.Column(
            "booking_closes_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    # Remove temporary defaults from the database schema.
    op.alter_column(
        "events",
        "category",
        existing_type=sa.String(length=50),
        server_default=None,
        existing_nullable=False,
    )
    op.alter_column(
        "events",
        "age_rating",
        existing_type=sa.String(length=20),
        server_default=None,
        existing_nullable=False,
    )
    op.alter_column(
        "shows",
        "silver_price",
        existing_type=sa.Numeric(precision=10, scale=2),
        server_default=None,
        existing_nullable=False,
    )
    op.alter_column(
        "shows",
        "gold_price",
        existing_type=sa.Numeric(precision=10, scale=2),
        server_default=None,
        existing_nullable=False,
    )
    op.alter_column(
        "shows",
        "recliner_price",
        existing_type=sa.Numeric(precision=10, scale=2),
        server_default=None,
        existing_nullable=False,
    )


def downgrade() -> None:
    op.drop_column("shows", "booking_closes_at")
    op.drop_column("shows", "recliner_price")
    op.drop_column("shows", "gold_price")
    op.drop_column("shows", "silver_price")
    op.drop_column("events", "age_rating")
    op.drop_column("events", "category")
