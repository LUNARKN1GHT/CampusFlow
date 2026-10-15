"""R002 candidate field revisions

Revision ID: r002_field_revisions
Revises: x001_candidate_evidence
Create Date: 2026-10-14 13:20:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "r002_field_revisions"
down_revision: str | None = "x001_candidate_evidence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "candidate_field_revisions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("field_id", sa.Integer(), nullable=False),
        sa.Column("previous_value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("new_value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("previous_origin", sa.String(length=20), nullable=False),
        sa.Column("new_origin", sa.String(length=20), nullable=False),
        sa.Column("operator", sa.String(length=100), nullable=False),
        sa.Column("note", sa.String(length=1000), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "new_origin IN ('document', 'user', 'inferred', 'suggestion')",
            name="ck_revision_new_origin",
        ),
        sa.CheckConstraint(
            "previous_origin IN ('document', 'user', 'inferred', 'suggestion')",
            name="ck_revision_previous_origin",
        ),
        sa.ForeignKeyConstraint(["field_id"], ["extracted_fields.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_candidate_field_revisions_field_id"),
        "candidate_field_revisions",
        ["field_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_candidate_field_revisions_field_id"), table_name="candidate_field_revisions"
    )
    op.drop_table("candidate_field_revisions")
