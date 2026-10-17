"""Q002 immutable embedding profiles.

Revision ID: q002_embedding_profiles
Revises: r007_course_rules
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "q002_embedding_profiles"
down_revision = "r007_course_rules"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "embedding_profiles",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("provider", sa.String(100), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("revision", sa.String(100), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("generation_config", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("dimensions > 0 AND dimensions <= 4096"),
    )
    op.execute("""
        CREATE FUNCTION reject_embedding_profile_update() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'embedding profiles are immutable; register a new revision';
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER embedding_profiles_immutable
        BEFORE UPDATE OR DELETE ON embedding_profiles
        FOR EACH ROW EXECUTE FUNCTION reject_embedding_profile_update()
    """)


def downgrade() -> None:
    op.drop_table("embedding_profiles")
    op.execute("DROP FUNCTION reject_embedding_profile_update()")
