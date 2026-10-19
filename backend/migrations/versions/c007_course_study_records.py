"""add course study records for completed, in-progress and planned courses

Revision ID: c007_course_study_records
Revises: c006_course_aliases
Create Date: 2026-10-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c007_course_study_records"
down_revision: str | None = "c006_course_aliases"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "course_study_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column(
            "curriculum_course_id",
            sa.Integer(),
            sa.ForeignKey("curriculum_courses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "semester_id",
            sa.Integer(),
            sa.ForeignKey("semesters.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("status", sa.String(20), nullable=False),
        # 成绩与已得学分只保存用户填写值；未知保持 NULL，不按课程学分推算。
        sa.Column("grade", sa.String(100), nullable=True),
        sa.Column("credits_earned", sa.Float(), nullable=True),
        sa.Column("note", sa.String(500), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint(
            "curriculum_course_id",
            "semester_id",
            "status",
            name="uq_course_study_record",
            postgresql_nulls_not_distinct=True,
        ),
        sa.CheckConstraint(
            "status IN ('completed', 'in_progress', 'planned')",
            name="ck_course_study_record_status",
        ),
        sa.CheckConstraint(
            "credits_earned IS NULL OR credits_earned >= 0",
            name="ck_course_study_record_credits",
        ),
    )
    op.create_index(
        "ix_course_study_records_workspace_id", "course_study_records", ["workspace_id"]
    )
    op.create_index(
        "ix_course_study_records_curriculum_course_id",
        "course_study_records",
        ["curriculum_course_id"],
    )
    op.create_index("ix_course_study_records_semester_id", "course_study_records", ["semester_id"])


def downgrade() -> None:
    op.drop_index("ix_course_study_records_semester_id", table_name="course_study_records")
    op.drop_index("ix_course_study_records_curriculum_course_id", table_name="course_study_records")
    op.drop_index("ix_course_study_records_workspace_id", table_name="course_study_records")
    op.drop_table("course_study_records")
