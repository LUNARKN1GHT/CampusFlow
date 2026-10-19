"""add course aliases with explicit scope

Revision ID: c006_course_aliases
Revises: c004_course_relations
Create Date: 2026-10-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c006_course_aliases"
down_revision: str | None = "c004_course_relations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "course_aliases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column(
            "curriculum_plan_id",
            sa.Integer(),
            sa.ForeignKey("curriculum_plans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "curriculum_course_id",
            sa.Integer(),
            sa.ForeignKey("curriculum_courses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("alias", sa.String(200), nullable=False),
        sa.Column("class_name", sa.String(100), nullable=True),
        sa.Column(
            "semester_id",
            sa.Integer(),
            sa.ForeignKey("semesters.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint(
            "curriculum_course_id",
            "alias",
            "class_name",
            "semester_id",
            name="uq_course_alias",
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_index("ix_course_aliases_workspace_id", "course_aliases", ["workspace_id"])
    op.create_index(
        "ix_course_aliases_curriculum_plan_id", "course_aliases", ["curriculum_plan_id"]
    )
    op.create_index(
        "ix_course_aliases_curriculum_course_id", "course_aliases", ["curriculum_course_id"]
    )
    op.create_index("ix_course_aliases_semester_id", "course_aliases", ["semester_id"])


def downgrade() -> None:
    op.drop_index("ix_course_aliases_semester_id", table_name="course_aliases")
    op.drop_index("ix_course_aliases_curriculum_course_id", table_name="course_aliases")
    op.drop_index("ix_course_aliases_curriculum_plan_id", table_name="course_aliases")
    op.drop_index("ix_course_aliases_workspace_id", table_name="course_aliases")
    op.drop_table("course_aliases")
