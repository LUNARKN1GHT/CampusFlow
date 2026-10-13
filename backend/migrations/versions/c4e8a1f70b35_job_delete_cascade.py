"""job delete cascade

J007：资料删除时，它的处理作业与尝试历史必须一并消失。
否则 DELETE FROM materials 会被 processing_jobs 的外键卡住（删除资料直接报错），
或者留下一个指向已删除资料的作业，晚到的 Worker 还可能把资料状态写回去。

Revision ID: c4e8a1f70b35
Revises: b7d24e91c4a8
Create Date: 2026-10-12 10:20:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4e8a1f70b35"
down_revision: str | None = "b7d24e91c4a8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("processing_jobs_material_id_fkey", "processing_jobs", type_="foreignkey")
    op.create_foreign_key(
        "processing_jobs_material_id_fkey",
        "processing_jobs",
        "materials",
        ["material_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_constraint("processing_jobs_version_id_fkey", "processing_jobs", type_="foreignkey")
    op.create_foreign_key(
        "processing_jobs_version_id_fkey",
        "processing_jobs",
        "material_versions",
        ["version_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_constraint(
        "processing_job_attempts_job_id_fkey", "processing_job_attempts", type_="foreignkey"
    )
    op.create_foreign_key(
        "processing_job_attempts_job_id_fkey",
        "processing_job_attempts",
        "processing_jobs",
        ["job_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint(
        "processing_job_attempts_job_id_fkey", "processing_job_attempts", type_="foreignkey"
    )
    op.create_foreign_key(
        "processing_job_attempts_job_id_fkey",
        "processing_job_attempts",
        "processing_jobs",
        ["job_id"],
        ["id"],
    )
    op.drop_constraint("processing_jobs_version_id_fkey", "processing_jobs", type_="foreignkey")
    op.create_foreign_key(
        "processing_jobs_version_id_fkey",
        "processing_jobs",
        "material_versions",
        ["version_id"],
        ["id"],
    )
    op.drop_constraint("processing_jobs_material_id_fkey", "processing_jobs", type_="foreignkey")
    op.create_foreign_key(
        "processing_jobs_material_id_fkey",
        "processing_jobs",
        "materials",
        ["material_id"],
        ["id"],
    )
