"""R007 fixed event source invalidation

Revision ID: r006_event_source_invalidation
Revises: r005_event_source_candidate
Create Date: 2026-10-14 16:05:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "r006_event_source_invalidation"
down_revision: str | None = "r005_event_source_candidate"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "fixed_events",
        sa.Column("source_invalidated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "fixed_events", sa.Column("source_invalidated_note", sa.String(length=300), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("fixed_events", "source_invalidated_note")
    op.drop_column("fixed_events", "source_invalidated_at")
