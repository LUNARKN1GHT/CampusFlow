"""merge processing jobs and chunk coordinates

Revision ID: 5c308f3468b5
Revises: 7aa842f421d7, 9e2e4f0eb119
Create Date: 2026-10-10 15:40:38.505110

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "5c308f3468b5"
down_revision: str | None = ("7aa842f421d7", "9e2e4f0eb119")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
