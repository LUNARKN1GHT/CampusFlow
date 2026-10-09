"""Persist independent weekly capacity; preserve existing daily settings.

Revision ID: w007_weekly_capacity
Revises: w005_workspace_default
"""

import sqlalchemy as sa
from alembic import op

revision = "w007_weekly_capacity"
down_revision = "w005_workspace_default"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workspaces",
        sa.Column("weekly_capacity_minutes", sa.Integer(), server_default="1680", nullable=False),
    )
    op.execute("UPDATE workspaces SET weekly_capacity_minutes = daily_capacity_minutes * 7")


def downgrade() -> None:
    op.drop_column("workspaces", "weekly_capacity_minutes")
