"""N014 version change acceptances.

Revision ID: n014_version_acceptances
Revises: m6_merge_n010_p001
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "n014_version_acceptances"
down_revision = "m6_merge_n010_p001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "version_change_acceptances",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column(
            "baseline_candidate_id",
            sa.Integer(),
            sa.ForeignKey("extracted_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "comparison_candidate_id",
            sa.Integer(),
            sa.ForeignKey("extracted_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "baseline_version_id",
            sa.Integer(),
            sa.ForeignKey("material_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "comparison_version_id",
            sa.Integer(),
            sa.ForeignKey("material_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("accepted_fields", JSONB(), nullable=False),
        sa.Column("reason", sa.String(1000), nullable=False),
        sa.Column("operator", sa.String(100), nullable=False),
        sa.Column("evidence_snapshot", JSONB(), nullable=False, server_default="[]"),
        sa.Column("previous_review_status", sa.String(20), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "baseline_candidate_id",
            "comparison_candidate_id",
            "accepted_fields",
            name="uq_version_change_acceptance",
        ),
    )
    op.create_index(
        "ix_version_change_acceptances_workspace_id",
        "version_change_acceptances",
        ["workspace_id"],
    )
    op.create_index(
        "ix_version_change_acceptances_baseline_candidate_id",
        "version_change_acceptances",
        ["baseline_candidate_id"],
    )
    op.create_index(
        "ix_version_change_acceptances_comparison_candidate_id",
        "version_change_acceptances",
        ["comparison_candidate_id"],
    )


def downgrade() -> None:
    op.drop_table("version_change_acceptances")
