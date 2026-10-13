"""add task remaining minutes

Revision ID: 7f35c9a1d201
Revises: c95d3606e7ee
Create Date: 2026-10-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7f35c9a1d201"
down_revision: str | None = "c95d3606e7ee"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("remaining_minutes", sa.Integer(), nullable=True))
    op.execute("UPDATE tasks SET remaining_minutes = estimated_minutes")


def downgrade() -> None:
    op.drop_column("tasks", "remaining_minutes")
