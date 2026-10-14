"""R004 task source candidate link

Revision ID: r004_task_source_candidate
Revises: r003_review_changes
Create Date: 2026-10-14 14:40:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "r004_task_source_candidate"
down_revision: str | None = "r003_review_changes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("source_candidate_id", sa.Integer(), nullable=True))
    op.create_index(
        op.f("ix_tasks_source_candidate_id"), "tasks", ["source_candidate_id"], unique=True
    )
    op.create_foreign_key(
        "tasks_source_candidate_id_fkey",
        "tasks",
        "extracted_items",
        ["source_candidate_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("tasks_source_candidate_id_fkey", "tasks", type_="foreignkey")
    op.drop_index(op.f("ix_tasks_source_candidate_id"), table_name="tasks")
    op.drop_column("tasks", "source_candidate_id")
