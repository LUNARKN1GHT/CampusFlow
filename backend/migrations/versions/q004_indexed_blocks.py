"""Q004 partial: per-block vector storage, queue integration follows.

Revision ID: q004_indexed_blocks
Revises: q002_embedding_profiles
"""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import VECTOR

revision = "q004_indexed_blocks"
down_revision = "q002_embedding_profiles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "indexed_blocks",
        sa.Column(
            "profile_id", sa.String(64), sa.ForeignKey("embedding_profiles.id"), primary_key=True
        ),
        sa.Column("block_id", sa.String(64), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column(
            "version_id",
            sa.Integer(),
            sa.ForeignKey("material_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_chunk_id",
            sa.Integer(),
            sa.ForeignKey("source_chunks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_fingerprint", sa.String(64), nullable=False),
        sa.Column("start_offset", sa.Integer(), nullable=False),
        sa.Column("end_offset", sa.Integer(), nullable=False),
        sa.Column("chunker_version", sa.String(100), nullable=False),
        sa.Column("text", sa.String(), nullable=False),
        sa.Column("state", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("retryable", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("failure_code", sa.String(40)),
        sa.Column("embedding", VECTOR(1024)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("start_offset >= 0 AND end_offset > start_offset"),
        sa.CheckConstraint("state IN ('pending', 'running', 'done', 'failed', 'skipped')"),
        sa.CheckConstraint("(state = 'done') = (embedding IS NOT NULL)"),
        sa.CheckConstraint("NOT retryable OR state = 'failed'"),
    )
    for name in ("workspace_id", "version_id", "source_chunk_id"):
        op.create_index(f"ix_indexed_blocks_{name}", "indexed_blocks", [name])


def downgrade() -> None:
    op.drop_table("indexed_blocks")
    # vector 可被其他表使用，不删除共享扩展。
