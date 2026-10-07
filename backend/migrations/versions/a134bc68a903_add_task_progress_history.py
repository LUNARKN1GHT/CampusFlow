"""add task progress history

Revision ID: a134bc68a903
Revises: 7f35c9a1d201
Create Date: 2026-10-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a134bc68a903"
down_revision: str | None = "7f35c9a1d201"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "task_progress_changes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("from_progress", sa.String(length=20), nullable=False),
        sa.Column("to_progress", sa.String(length=20), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=True),
        sa.Column("previous_remaining_minutes", sa.Integer(), nullable=True),
        sa.Column("new_remaining_minutes", sa.Integer(), nullable=True),
        sa.Column(
            "changed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_task_progress_changes_task_id"),
        "task_progress_changes",
        ["task_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_task_progress_changes_task_id"), table_name="task_progress_changes")
    op.drop_table("task_progress_changes")
