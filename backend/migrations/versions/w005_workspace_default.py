"""workspaces 增加 is_default 与默认空间唯一约束

Revision ID: w005_workspace_default
Revises: c95d3606e7ee
Create Date: 2026-10-08

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "w005_workspace_default"
down_revision: str | None = "f0b8c24d2b74"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workspaces",
        sa.Column(
            "is_default",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    )
    # 既有数据：最早的一行视为默认空间
    op.execute(
        "UPDATE workspaces SET is_default = true WHERE id = (SELECT min(id) FROM workspaces)"
    )
    # 部分唯一索引：保证并发初始化时最多只有一个默认空间
    op.create_index(
        "uq_workspaces_default",
        "workspaces",
        ["is_default"],
        unique=True,
        postgresql_where=sa.text("is_default"),
    )


def downgrade() -> None:
    op.drop_index("uq_workspaces_default", table_name="workspaces")
    op.drop_column("workspaces", "is_default")
