"""N010 source-version relations and replacement declarations.

Revision ID: n010_version_relations
Revises: r007_course_rules
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "n010_version_relations"
down_revision = "r007_course_rules"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "material_version_relations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column(
            "source_version_id",
            sa.Integer(),
            sa.ForeignKey("material_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "target_version_id",
            sa.Integer(),
            sa.ForeignKey("material_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("statement", sa.String(1000), nullable=False),
        sa.Column("actor", sa.String(200), nullable=False),
        sa.Column("source_snapshot", JSONB(), nullable=False),
        sa.Column("target_snapshot", JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "source_version_id",
            "target_version_id",
            "kind",
            name="uq_material_version_relation",
        ),
        sa.CheckConstraint("source_version_id <> target_version_id"),
        sa.CheckConstraint("kind IN ('same_source', 'replacement_claim')"),
        sa.CheckConstraint("kind <> 'same_source' OR source_version_id < target_version_id"),
    )
    for name in ("workspace_id", "source_version_id", "target_version_id"):
        op.create_index(
            f"ix_material_version_relations_{name}", "material_version_relations", [name]
        )


def downgrade() -> None:
    op.drop_table("material_version_relations")
