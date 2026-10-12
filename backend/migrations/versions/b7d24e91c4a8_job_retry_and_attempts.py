"""job retry and attempts

J005：区分可重试失败与永久失败，并保留每次尝试的历史。
- processing_jobs.started_at：领取时刻，用于识别长期停在"处理中"的作业；
- processing_jobs.retryable：最近一次失败是否值得重试（成功/部分成功为 NULL）；
- processing_job_attempts：每次终态尝试一条记录，保留成功阶段与失败原因。

Revision ID: b7d24e91c4a8
Revises: 9f1c47b0a2d3
Create Date: 2026-10-11 15:40:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7d24e91c4a8"
down_revision: str | None = "9f1c47b0a2d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "processing_jobs",
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("processing_jobs", sa.Column("retryable", sa.Boolean(), nullable=True))
    # 超时回收按"处理中 + 领取时间早于阈值"筛选，故建于 started_at
    op.create_index(
        op.f("ix_processing_jobs_started_at"),
        "processing_jobs",
        ["started_at"],
        unique=False,
    )

    op.create_table(
        "processing_job_attempts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("processing_jobs.id"), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("stage", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("scope", sa.String(length=500), nullable=True),
        sa.Column("error_reason", sa.String(length=500), nullable=True),
        sa.Column("retryable", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        # 同一次尝试只能留一条历史：重复写入不会产生重复记录
        sa.UniqueConstraint("job_id", "attempt_no", name="uq_job_attempts_no"),
    )
    op.create_index(
        op.f("ix_processing_job_attempts_job_id"),
        "processing_job_attempts",
        ["job_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_processing_job_attempts_job_id"), table_name="processing_job_attempts")
    op.drop_table("processing_job_attempts")
    op.drop_index(op.f("ix_processing_jobs_started_at"), table_name="processing_jobs")
    op.drop_column("processing_jobs", "retryable")
    op.drop_column("processing_jobs", "started_at")
