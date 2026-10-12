"""merge parsing and job deletion revisions for J009

Revision ID: 39cd7bcbfd70
Revises: 0805519c500a, d2b5f8c31a04
Create Date: 2026-10-12 15:13:32.445908

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "39cd7bcbfd70"
down_revision: str | None = ("0805519c500a", "d2b5f8c31a04")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
