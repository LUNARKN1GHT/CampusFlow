"""job outbox fields

J003：为处理作业增加待投递记录字段，实现"数据库提交与入队分离"。
enqueued_at 为空表示尚未成功投递；delivery_attempts 记录投递尝试次数。

Revision ID: 9f1c47b0a2d3
Revises: 5c308f3468b5
Create Date: 2026-10-11 11:05:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9f1c47b0a2d3"
down_revision: str | None = "5c308f3468b5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "processing_jobs",
        sa.Column("enqueued_at", sa.DateTime(timezone=True), nullable=True),
    )
    # 先带默认值加列（兼容已有作业行），随后去掉默认值，与模型声明保持一致
    op.add_column(
        "processing_jobs",
        sa.Column("delivery_attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.alter_column("processing_jobs", "delivery_attempts", server_default=None)
    # 补偿扫描按 enqueued_at IS NULL 取待投递作业，故建于该列
    op.create_index(
        op.f("ix_processing_jobs_enqueued_at"),
        "processing_jobs",
        ["enqueued_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_processing_jobs_enqueued_at"), table_name="processing_jobs")
    op.drop_column("processing_jobs", "delivery_attempts")
    op.drop_column("processing_jobs", "enqueued_at")
