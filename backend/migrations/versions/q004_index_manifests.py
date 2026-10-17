"""Q004 complete manifests and expiring block claims.

Revision ID: q004_index_manifests
Revises: q004_indexed_blocks
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "q004_index_manifests"
down_revision = "q004_indexed_blocks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("indexed_blocks", sa.Column("claim_token", sa.String(32)))
    op.add_column("indexed_blocks", sa.Column("lease_until", sa.DateTime(timezone=True)))
    # 前一阶段未接 Worker；若有旧 running 记录，显式转为可补做状态。
    op.execute(
        "UPDATE indexed_blocks SET state='failed', retryable=true, failure_code='lease_expired' "
        "WHERE state='running'"
    )
    op.create_check_constraint(
        "ck_indexed_blocks_claim",
        "indexed_blocks",
        "(state = 'running' AND claim_token IS NOT NULL AND lease_until IS NOT NULL) OR "
        "(state <> 'running' AND claim_token IS NULL AND lease_until IS NULL)",
    )
    op.create_table(
        "index_manifests",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column(
            "version_id",
            sa.Integer(),
            sa.ForeignKey("material_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "profile_id", sa.String(64), sa.ForeignKey("embedding_profiles.id"), nullable=False
        ),
        sa.Column("snapshot", JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    for name in ("workspace_id", "version_id"):
        op.create_index(f"ix_index_manifests_{name}", "index_manifests", [name])


def downgrade() -> None:
    op.drop_table("index_manifests")
    op.drop_constraint("ck_indexed_blocks_claim", "indexed_blocks", type_="check")
    op.execute(
        "UPDATE indexed_blocks SET state='failed', retryable=true, failure_code='lease_expired' "
        "WHERE state='running'"
    )
    op.drop_column("indexed_blocks", "lease_until")
    op.drop_column("indexed_blocks", "claim_token")
