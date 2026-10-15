"""R003 candidate review changes

Revision ID: r003_review_changes
Revises: r002_field_revisions
Create Date: 2026-10-14 14:05:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "r003_review_changes"
down_revision: str | None = "r002_field_revisions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "candidate_review_changes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("candidate_id", sa.Integer(), nullable=False),
        sa.Column("from_status", sa.String(length=20), nullable=False),
        sa.Column("to_status", sa.String(length=20), nullable=False),
        sa.Column("operator", sa.String(length=100), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=True),
        sa.Column(
            "changed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "from_status IN ('pending', 'confirmed', 'ignored', 'superseded')",
            name="ck_review_change_from_status",
        ),
        sa.CheckConstraint(
            "to_status IN ('pending', 'confirmed', 'ignored', 'superseded')",
            name="ck_review_change_to_status",
        ),
        sa.ForeignKeyConstraint(["candidate_id"], ["extracted_items.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_candidate_review_changes_candidate_id"),
        "candidate_review_changes",
        ["candidate_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_candidate_review_changes_candidate_id"), table_name="candidate_review_changes"
    )
    op.drop_table("candidate_review_changes")
