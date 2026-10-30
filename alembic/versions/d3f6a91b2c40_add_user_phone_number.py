"""Add optional phone number to users."""

from alembic import op
import sqlalchemy as sa


revision = "d3f6a91b2c40"
down_revision = "8c31f6a2d905"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "phone_number",
            sa.String(length=10),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "phone_number")