"""merge m1 and material processing heads

Revision ID: 8d7dd21f0e73
Revises: 39cd7bcbfd70, t007_task_target_due_source
Create Date: 2026-10-12 17:43:28.787744

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "8d7dd21f0e73"
down_revision: str | None = ("39cd7bcbfd70", "t007_task_target_due_source")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
