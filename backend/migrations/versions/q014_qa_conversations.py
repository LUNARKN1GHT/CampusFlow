"""Q014 scoped QA conversations and turns.

Revision ID: q014_qa_conversations
Revises: q004_index_hnsw
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "q014_qa_conversations"
down_revision = "q004_index_hnsw"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "qa_conversations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("semester_id", sa.Integer(), sa.ForeignKey("semesters.id")),
        sa.Column("course_id", sa.Integer(), sa.ForeignKey("courses.id")),
        sa.Column("class_name", sa.String(100)),
        sa.Column("mode", sa.String(20), server_default="hybrid", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "mode IN ('keyword', 'vector', 'hybrid')", name="ck_qa_conversation_mode"
        ),
    )
    op.create_index("ix_qa_conversations_workspace_id", "qa_conversations", ["workspace_id"])
    op.create_table(
        "qa_turns",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.Integer(),
            sa.ForeignKey("qa_conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("question", sa.String(2000), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("insufficient", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("intent", sa.String(20), server_default="query", nullable=False),
        sa.Column("scope", JSONB(), nullable=False),
        sa.Column("citations", JSONB(), server_default="[]", nullable=False),
        sa.Column("coverage", JSONB(), server_default="[]", nullable=False),
        sa.Column("comparisons", JSONB(), server_default="[]", nullable=False),
        sa.Column("conflicts", JSONB(), server_default="[]", nullable=False),
        sa.Column("uncertainties", JSONB(), server_default="[]", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_qa_turns_conversation_id", "qa_turns", ["conversation_id"])


def downgrade() -> None:
    op.drop_table("qa_turns")
    op.drop_table("qa_conversations")
