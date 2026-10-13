"""task source invalidation

D009：删除资料时保留的个人任务必须留下"来源已被删除"的痕迹。
只把 tasks.source_material_id 置空的话，这条任务在界面上和手工新建的任务
完全一样，用户看不出它的引用已经不可用了。

Revision ID: d2b5f8c31a04
Revises: c4e8a1f70b35
Create Date: 2026-10-12 14:40:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d2b5f8c31a04"
down_revision: str | None = "c4e8a1f70b35"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column("source_invalidated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "tasks",
        sa.Column("source_invalidated_note", sa.String(length=300), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tasks", "source_invalidated_note")
    op.drop_column("tasks", "source_invalidated_at")
