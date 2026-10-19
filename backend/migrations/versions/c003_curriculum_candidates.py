"""add curriculum candidate kinds and plan scope

Revision ID: c003_curriculum_candidates
Revises: c006_course_aliases
Create Date: 2026-10-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c003_curriculum_candidates"
down_revision: str | None = "c006_course_aliases"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_candidate_kind", "extracted_items", type_="check")
    op.create_check_constraint(
        "ck_candidate_kind",
        "extracted_items",
        "kind IN ('task', 'event', 'rule', 'curriculum_course', 'course_relation')",
    )
    op.add_column(
        "extracted_items", sa.Column("curriculum_plan_id", sa.Integer(), nullable=True)
    )
    op.create_index(
        "ix_extracted_items_curriculum_plan_id",
        "extracted_items",
        ["curriculum_plan_id"],
    )
    op.create_foreign_key(
        "extracted_items_curriculum_plan_id_fkey",
        "extracted_items",
        "curriculum_plans",
        ["curriculum_plan_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint(
        "extracted_items_curriculum_plan_id_fkey", "extracted_items", type_="foreignkey"
    )
    op.drop_index("ix_extracted_items_curriculum_plan_id", table_name="extracted_items")
    op.drop_column("extracted_items", "curriculum_plan_id")
    op.drop_constraint("ck_candidate_kind", "extracted_items", type_="check")
    op.create_check_constraint(
        "ck_candidate_kind",
        "extracted_items",
        "kind IN ('task', 'event', 'rule')",
    )
