"""N005: preserve immutable source metadata with each material version."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "n005_web_snapshot_metadata"
down_revision = "8d7dd21f0e73"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "material_versions",
        sa.Column(
            "source_metadata", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
    )


def downgrade() -> None:
    op.drop_column("material_versions", "source_metadata")
