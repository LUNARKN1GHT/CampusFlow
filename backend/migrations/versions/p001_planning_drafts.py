"""P001 plan drafts, versions and time blocks.

Revision ID: p001_planning_drafts
Revises: q014_qa_conversations
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "p001_planning_drafts"
down_revision = "q014_qa_conversations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "plan_drafts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("semester_id", sa.Integer(), sa.ForeignKey("semesters.id"), nullable=True),
        sa.Column("range_start", sa.Date(), nullable=False),
        sa.Column("range_end", sa.Date(), nullable=False),
        sa.Column("status", sa.String(20), server_default="draft", nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("input_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.Column("assumptions", postgresql.JSONB(), server_default="[]", nullable=False),
        sa.Column("gaps", postgresql.JSONB(), server_default="[]", nullable=False),
        sa.Column("unscheduled", postgresql.JSONB(), server_default="[]", nullable=False),
        sa.Column("conflicts", postgresql.JSONB(), server_default="[]", nullable=False),
        sa.Column("coverage", postgresql.JSONB(), server_default="{}", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("accepted_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("range_end >= range_start", name="ck_plan_drafts_range"),
        sa.CheckConstraint("version >= 1", name="ck_plan_drafts_version"),
        sa.CheckConstraint(
            "status IN ('draft', 'accepted', 'superseded')", name="ck_plan_drafts_status"
        ),
    )
    op.create_index("ix_plan_drafts_workspace_id", "plan_drafts", ["workspace_id"])
    op.create_index("ix_plan_drafts_semester_id", "plan_drafts", ["semester_id"])

    op.create_table(
        "plan_blocks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "draft_id",
            sa.Integer(),
            sa.ForeignKey("plan_drafts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "task_id",
            sa.Integer(),
            sa.ForeignKey("tasks.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("kind", sa.String(20), server_default="task", nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("minutes", sa.Integer(), nullable=False),
        sa.Column("seq", sa.Integer(), server_default="0", nullable=False),
        sa.Column("locked", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("reason", sa.String(1000), nullable=False),
        sa.Column("constraint_source", postgresql.JSONB(), server_default="[]", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("ends_at > starts_at", name="ck_plan_blocks_interval"),
        sa.CheckConstraint("minutes > 0", name="ck_plan_blocks_minutes"),
        sa.CheckConstraint("kind IN ('task', 'buffer')", name="ck_plan_blocks_kind"),
    )
    op.create_index("ix_plan_blocks_draft_id", "plan_blocks", ["draft_id"])
    op.create_index("ix_plan_blocks_task_id", "plan_blocks", ["task_id"])

    op.create_table(
        "plan_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "draft_id",
            sa.Integer(),
            sa.ForeignKey("plan_drafts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("change", sa.String(30), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("draft_id", "version_no"),
    )
    op.create_index("ix_plan_versions_draft_id", "plan_versions", ["draft_id"])


def downgrade() -> None:
    op.drop_table("plan_versions")
    op.drop_table("plan_blocks")
    op.drop_table("plan_drafts")
