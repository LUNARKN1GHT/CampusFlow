"""add curriculum courses and typed course relations

Revision ID: c004_course_relations
Revises: c002_curriculum_plans
Create Date: 2026-10-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c004_course_relations"
down_revision: str | None = "c002_curriculum_plans"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "curriculum_courses",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column(
            "curriculum_plan_id",
            sa.Integer(),
            sa.ForeignKey("curriculum_plans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("code", sa.String(50), nullable=True),
        sa.Column("credits", sa.Float(), nullable=True),
        sa.Column(
            "offered_terms",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "source_candidate_id",
            sa.Integer(),
            sa.ForeignKey("extracted_items.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("curriculum_plan_id", "name", name="uq_curriculum_course_name"),
        sa.CheckConstraint("credits IS NULL OR credits >= 0", name="ck_curriculum_course_credits"),
    )
    op.create_index("ix_curriculum_courses_workspace_id", "curriculum_courses", ["workspace_id"])
    op.create_index(
        "ix_curriculum_courses_curriculum_plan_id",
        "curriculum_courses",
        ["curriculum_plan_id"],
    )
    op.create_index(
        "ix_curriculum_courses_source_candidate_id",
        "curriculum_courses",
        ["source_candidate_id"],
        unique=True,
    )

    op.create_table(
        "course_relations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column(
            "curriculum_plan_id",
            sa.Integer(),
            sa.ForeignKey("curriculum_plans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "from_course_id",
            sa.Integer(),
            sa.ForeignKey("curriculum_courses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "to_course_id",
            sa.Integer(),
            sa.ForeignKey("curriculum_courses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("relation_type", sa.String(20), nullable=False),
        sa.Column("condition_text", sa.String(1000), nullable=True),
        sa.Column(
            "source_candidate_id",
            sa.Integer(),
            sa.ForeignKey("extracted_items.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_by", sa.String(100), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint(
            "curriculum_plan_id",
            "from_course_id",
            "to_course_id",
            "relation_type",
            name="uq_course_relation",
        ),
        sa.CheckConstraint(
            "relation_type IN ('prerequisite', 'corequisite', 'recommended')",
            name="ck_course_relation_type",
        ),
    )
    op.create_index("ix_course_relations_workspace_id", "course_relations", ["workspace_id"])
    op.create_index(
        "ix_course_relations_curriculum_plan_id", "course_relations", ["curriculum_plan_id"]
    )
    op.create_index("ix_course_relations_from_course_id", "course_relations", ["from_course_id"])
    op.create_index("ix_course_relations_to_course_id", "course_relations", ["to_course_id"])
    op.create_index(
        "ix_course_relations_source_candidate_id",
        "course_relations",
        ["source_candidate_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_course_relations_source_candidate_id", table_name="course_relations")
    op.drop_index("ix_course_relations_to_course_id", table_name="course_relations")
    op.drop_index("ix_course_relations_from_course_id", table_name="course_relations")
    op.drop_index("ix_course_relations_curriculum_plan_id", table_name="course_relations")
    op.drop_index("ix_course_relations_workspace_id", table_name="course_relations")
    op.drop_table("course_relations")
    op.drop_index("ix_curriculum_courses_source_candidate_id", table_name="curriculum_courses")
    op.drop_index("ix_curriculum_courses_curriculum_plan_id", table_name="curriculum_courses")
    op.drop_index("ix_curriculum_courses_workspace_id", table_name="curriculum_courses")
    op.drop_table("curriculum_courses")
