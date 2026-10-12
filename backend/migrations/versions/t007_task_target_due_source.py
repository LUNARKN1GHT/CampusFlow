"""add task personal target date and due source

Revision ID: t007_task_target_due_source
Revises: w007_weekly_capacity
Create Date: 2026-10-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "t007_task_target_due_source"
down_revision: str | None = "w007_weekly_capacity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("target_date", sa.Date(), nullable=True))
    op.add_column(
        "tasks",
        sa.Column("due_source", sa.String(length=20), nullable=False, server_default="user"),
    )
    op.add_column("tasks", sa.Column("due_note", sa.String(length=500), nullable=True))


def downgrade() -> None:
    op.drop_column("tasks", "due_note")
    op.drop_column("tasks", "due_source")
    op.drop_column("tasks", "target_date")
