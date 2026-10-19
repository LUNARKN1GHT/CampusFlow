"""N016 candidate merge records.

Revision ID: n016_candidate_merges
Revises: n014_version_acceptances
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "n016_candidate_merges"
down_revision = "n014_version_acceptances"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "candidate_merges",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column(
            "target_candidate_id",
            sa.Integer(),
            sa.ForeignKey("extracted_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_candidate_id",
            sa.Integer(),
            sa.ForeignKey("extracted_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("target_formal_kind", sa.String(20)),
        sa.Column("target_formal_id", sa.Integer()),
        sa.Column("source_formal_id", sa.Integer()),
        sa.Column("preserved_progress", JSONB(), nullable=False, server_default="{}"),
        sa.Column("source_snapshot", JSONB(), nullable=False, server_default="{}"),
        sa.Column("reason", sa.String(1000), nullable=False),
        sa.Column("operator", sa.String(100), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "target_candidate_id",
            "source_candidate_id",
            name="uq_candidate_merge",
        ),
    )
    op.create_index("ix_candidate_merges_workspace_id", "candidate_merges", ["workspace_id"])
    op.create_index(
        "ix_candidate_merges_target_candidate_id",
        "candidate_merges",
        ["target_candidate_id"],
    )
    op.create_index(
        "ix_candidate_merges_source_candidate_id",
        "candidate_merges",
        ["source_candidate_id"],
    )


def downgrade() -> None:
    op.drop_table("candidate_merges")
