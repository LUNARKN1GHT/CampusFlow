"""version checksum for duplicate detection

Revision ID: 35042b675637
Revises: 219d64ce1832
Create Date: 2026-10-10 08:45:10.744790

注意：本迁移只做 checksum 相关变更。生成时本地开发库含有其他分支
（D017）的列与约束，autogenerate 曾误报删除操作，已人工移除——
本分支不得触碰 source_chunks 的坐标列与唯一约束（属于 D017 迁移）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "35042b675637"
down_revision: str | None = "219d64ce1832"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("material_versions", sa.Column("checksum", sa.String(length=64), nullable=True))
    op.create_index(
        op.f("ix_material_versions_checksum"), "material_versions", ["checksum"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_material_versions_checksum"), table_name="material_versions")
    op.drop_column("material_versions", "checksum")
