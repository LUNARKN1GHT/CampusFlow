"""Q004 pipeline: block attempts, job heartbeat and one embed job per version.

Revision ID: q004_index_pipeline
Revises: q004_index_manifests
"""

import sqlalchemy as sa
from alembic import op

revision = "q004_index_pipeline"
down_revision = "q004_index_manifests"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 每块尝试次数：按领取计数，达到上限后不再调用模型。
    op.add_column(
        "indexed_blocks",
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_check_constraint("ck_indexed_blocks_attempts", "indexed_blocks", "attempts >= 0")

    # 作业侧：清单关联、心跳与自动重投计数。
    op.add_column("processing_jobs", sa.Column("manifest_id", sa.String(64), nullable=True))
    op.create_foreign_key(
        "fk_processing_jobs_manifest",
        "processing_jobs",
        "index_manifests",
        ["manifest_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column(
        "processing_jobs", sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "processing_jobs",
        sa.Column("auto_retry_count", sa.Integer(), nullable=False, server_default="0"),
    )

    # 一份版本最多一个索引作业。既有重复数据只能由人工确认后清理，迁移不静默删除。
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM processing_jobs
                WHERE stage = 'embed'
                GROUP BY version_id
                HAVING count(*) > 1
            ) THEN
                RAISE EXCEPTION
                    '存在重复的 embed 作业，请先人工确认并清理后再升级';
            END IF;
        END
        $$
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_processing_jobs_embed_version "
        "ON processing_jobs (version_id) WHERE stage = 'embed'"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_processing_jobs_embed_version")
    op.drop_column("processing_jobs", "auto_retry_count")
    op.drop_column("processing_jobs", "heartbeat_at")
    op.drop_constraint("fk_processing_jobs_manifest", "processing_jobs", type_="foreignkey")
    op.drop_column("processing_jobs", "manifest_id")
    op.drop_constraint("ck_indexed_blocks_attempts", "indexed_blocks", type_="check")
    op.drop_column("indexed_blocks", "attempts")
