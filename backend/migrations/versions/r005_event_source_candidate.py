"""R005 fixed event source candidate link

Revision ID: r005_event_source_candidate
Revises: r004_task_source_candidate
Create Date: 2026-10-14 15:20:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "r005_event_source_candidate"
down_revision: str | None = "r004_task_source_candidate"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("fixed_events", sa.Column("source_candidate_id", sa.Integer(), nullable=True))
    op.create_index(
        op.f("ix_fixed_events_source_candidate_id"),
        "fixed_events",
        ["source_candidate_id"],
        unique=True,
    )
    op.create_foreign_key(
        "fixed_events_source_candidate_id_fkey",
        "fixed_events",
        "extracted_items",
        ["source_candidate_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fixed_events_source_candidate_id_fkey", "fixed_events", type_="foreignkey")
    op.drop_index(op.f("ix_fixed_events_source_candidate_id"), table_name="fixed_events")
    op.drop_column("fixed_events", "source_candidate_id")
