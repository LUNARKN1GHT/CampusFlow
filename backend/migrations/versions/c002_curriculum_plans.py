"""add curriculum plans and learner profile

Revision ID: c002_curriculum_plans
Revises: a010_reminders
Create Date: 2026-10-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c002_curriculum_plans"
down_revision: str | None = "a010_reminders"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "curriculum_plans",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("major", sa.String(100), nullable=False),
        sa.Column("grade_year", sa.Integer(), nullable=False),
        sa.Column("version_label", sa.String(100), nullable=False),
        sa.Column("credit_limit_per_term", sa.Integer(), nullable=True),
        sa.Column(
            "source_material_id",
            sa.Integer(),
            sa.ForeignKey("materials.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "source_version_id",
            sa.Integer(),
            sa.ForeignKey("material_versions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint(
            "workspace_id", "major", "grade_year", "version_label", name="uq_curriculum_plan"
        ),
        sa.CheckConstraint(
            "grade_year BETWEEN 1900 AND 2100", name="ck_curriculum_plan_grade_year"
        ),
        sa.CheckConstraint(
            "credit_limit_per_term IS NULL OR credit_limit_per_term > 0",
            name="ck_curriculum_plan_credit_limit",
        ),
    )
    op.create_index("ix_curriculum_plans_workspace_id", "curriculum_plans", ["workspace_id"])
    op.create_index(
        "ix_curriculum_plans_source_material_id", "curriculum_plans", ["source_material_id"]
    )
    op.create_index(
        "ix_curriculum_plans_source_version_id", "curriculum_plans", ["source_version_id"]
    )

    op.create_table(
        "learner_profiles",
        sa.Column(
            "workspace_id",
            sa.Integer(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("major", sa.String(100), nullable=True),
        sa.Column("grade_year", sa.Integer(), nullable=True),
        sa.Column(
            "curriculum_plan_id",
            sa.Integer(),
            sa.ForeignKey("curriculum_plans.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "grade_year IS NULL OR grade_year BETWEEN 1900 AND 2100",
            name="ck_learner_profile_grade_year",
        ),
    )
    op.create_index(
        "ix_learner_profiles_curriculum_plan_id", "learner_profiles", ["curriculum_plan_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_learner_profiles_curriculum_plan_id", table_name="learner_profiles")
    op.drop_table("learner_profiles")
    op.drop_index("ix_curriculum_plans_source_version_id", table_name="curriculum_plans")
    op.drop_index("ix_curriculum_plans_source_material_id", table_name="curriculum_plans")
    op.drop_index("ix_curriculum_plans_workspace_id", table_name="curriculum_plans")
    op.drop_table("curriculum_plans")
