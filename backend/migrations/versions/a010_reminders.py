"""add reminder rules and triggers

Revision ID: a010_reminders
Revises: a001_task_dependencies
Create Date: 2026-10-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a010_reminders"
down_revision: str | None = "a001_task_dependencies"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "reminder_rules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id"), nullable=False
        ),
        sa.Column(
            "task_id",
            sa.Integer(),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "advance_minutes", sa.Integer(), nullable=False, server_default="1440"
        ),
        sa.Column("repeat_minutes", sa.Integer(), nullable=True),
        sa.Column("paused", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("advance_minutes >= 0", name="ck_reminder_rules_advance"),
        sa.CheckConstraint(
            "repeat_minutes IS NULL OR repeat_minutes > 0",
            name="ck_reminder_rules_repeat",
        ),
        sa.CheckConstraint("version >= 1", name="ck_reminder_rules_version"),
    )
    op.create_index("ix_reminder_rules_workspace_id", "reminder_rules", ["workspace_id"])
    op.create_index("ix_reminder_rules_task_id", "reminder_rules", ["task_id"])
    op.create_index(
        "uq_reminder_rules_task",
        "reminder_rules",
        ["workspace_id", "task_id"],
        unique=True,
        postgresql_where=sa.text("task_id IS NOT NULL"),
    )
    op.create_index(
        "uq_reminder_rules_default",
        "reminder_rules",
        ["workspace_id"],
        unique=True,
        postgresql_where=sa.text("task_id IS NULL"),
    )

    op.create_table(
        "reminder_triggers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id"), nullable=False
        ),
        sa.Column(
            "rule_id",
            sa.Integer(),
            sa.ForeignKey("reminder_rules.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "task_id",
            sa.Integer(),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("dedupe_key", sa.String(length=200), nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("message", sa.String(length=500), nullable=False),
        sa.Column(
            "status", sa.String(length=20), nullable=False, server_default="pending"
        ),
        sa.Column(
            "payload",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "kind IN ('deadline', 'review')", name="ck_reminder_triggers_kind"
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'sent', 'dismissed', 'cancelled')",
            name="ck_reminder_triggers_status",
        ),
        sa.UniqueConstraint("dedupe_key", name="uq_reminder_triggers_dedupe"),
    )
    op.create_index(
        "ix_reminder_triggers_workspace_id", "reminder_triggers", ["workspace_id"]
    )
    op.create_index("ix_reminder_triggers_rule_id", "reminder_triggers", ["rule_id"])
    op.create_index("ix_reminder_triggers_task_id", "reminder_triggers", ["task_id"])
    op.create_index(
        "ix_reminder_triggers_scheduled_for", "reminder_triggers", ["scheduled_for"]
    )


def downgrade() -> None:
    op.drop_index("ix_reminder_triggers_scheduled_for", table_name="reminder_triggers")
    op.drop_index("ix_reminder_triggers_task_id", table_name="reminder_triggers")
    op.drop_index("ix_reminder_triggers_rule_id", table_name="reminder_triggers")
    op.drop_index("ix_reminder_triggers_workspace_id", table_name="reminder_triggers")
    op.drop_table("reminder_triggers")
    op.drop_index("uq_reminder_rules_default", table_name="reminder_rules")
    op.drop_index("uq_reminder_rules_task", table_name="reminder_rules")
    op.drop_index("ix_reminder_rules_task_id", table_name="reminder_rules")
    op.drop_index("ix_reminder_rules_workspace_id", table_name="reminder_rules")
    op.drop_table("reminder_rules")
