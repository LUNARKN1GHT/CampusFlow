"""add workspace study settings

Revision ID: f0b8c24d2b74
Revises: a134bc68a903
Create Date: 2026-10-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f0b8c24d2b74"
down_revision: str | None = "a134bc68a903"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workspaces",
        sa.Column("daily_capacity_minutes", sa.Integer(), server_default="240", nullable=False),
    )
    op.add_column(
        "workspaces", sa.Column("break_minutes", sa.Integer(), server_default="15", nullable=False)
    )
    op.add_column(
        "workspaces", sa.Column("buffer_minutes", sa.Integer(), server_default="30", nullable=False)
    )


def downgrade() -> None:
    op.drop_column("workspaces", "buffer_minutes")
    op.drop_column("workspaces", "break_minutes")
    op.drop_column("workspaces", "daily_capacity_minutes")
