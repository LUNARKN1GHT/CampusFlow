"""chunk table group

Revision ID: 0805519c500a
Revises: 9e2e4f0eb119
Create Date: 2026-10-10

注意：只做 table_group 列变更。并行分支的 checksum（D006）与坐标列（D017）
迁移在分支合并时另行对账，本迁移不得触碰。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0805519c500a"
down_revision: str | None = "9e2e4f0eb119"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("source_chunks", sa.Column("table_group", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("source_chunks", "table_group")
