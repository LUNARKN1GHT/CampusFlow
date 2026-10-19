"""K002–K013 external calendar connections, drafts, operations and links

Revision ID: k001_external_calendar
Revises: c007_merge_c003
Create Date: 2026-10-19

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "k001_external_calendar"
down_revision: str | Sequence[str] | None = "c007_merge_c003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "calendar_connections",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("account_email", sa.String(length=320), nullable=False),
        sa.Column("calendar_id", sa.String(length=500), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="active", nullable=False),
        sa.Column("scopes", sa.String(length=1000), server_default="", nullable=False),
        sa.Column("access_token_ciphertext", sa.Text(), nullable=True),
        sa.Column("refresh_token_ciphertext", sa.Text(), nullable=True),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('active', 'revoked', 'expired')", name="ck_calendar_connection_status"
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id", "provider", "account_email", name="uq_calendar_connection_account"
        ),
    )
    op.create_index(
        op.f("ix_calendar_connections_workspace_id"),
        "calendar_connections",
        ["workspace_id"],
        unique=False,
    )

    op.create_table(
        "calendar_oauth_states",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("state_hash", sa.String(length=128), nullable=False),
        sa.Column("redirect_uri", sa.String(length=1000), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("state_hash", name="uq_calendar_oauth_state_hash"),
    )
    op.create_index(
        op.f("ix_calendar_oauth_states_workspace_id"),
        "calendar_oauth_states",
        ["workspace_id"],
        unique=False,
    )

    op.create_table(
        "calendar_drafts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("connection_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="draft", nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("content_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("confirmed_fingerprint", sa.String(length=64), nullable=True),
        sa.Column(
            "confirmed_item_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "excluded",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column(
            "warnings",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column("note", sa.String(length=1000), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'confirmed', 'executing', 'completed', 'partial', "
            "'failed', 'unknown', 'cancelled', 'expired')",
            name="ck_calendar_draft_status",
        ),
        sa.CheckConstraint("version >= 1", name="ck_calendar_draft_version"),
        sa.ForeignKeyConstraint(["connection_id"], ["calendar_connections.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_calendar_drafts_connection_id"), "calendar_drafts", ["connection_id"], unique=False
    )
    op.create_index(
        op.f("ix_calendar_drafts_workspace_id"), "calendar_drafts", ["workspace_id"], unique=False
    )

    op.create_table(
        "calendar_draft_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("draft_id", sa.Integer(), nullable=False),
        sa.Column("operation_type", sa.String(length=20), nullable=False),
        sa.Column("source_type", sa.String(length=20), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("date_only", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("location", sa.String(length=500), nullable=True),
        sa.Column("target_calendar_id", sa.String(length=500), nullable=False),
        sa.Column("external_event_id", sa.String(length=500), nullable=True),
        sa.Column("external_etag", sa.String(length=500), nullable=True),
        sa.Column("item_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("content_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("position", sa.Integer(), server_default="0", nullable=False),
        sa.CheckConstraint(
            "operation_type IN ('create', 'update', 'delete')",
            name="ck_calendar_draft_item_operation",
        ),
        sa.CheckConstraint(
            "source_type IN ('task', 'plan_block')", name="ck_calendar_draft_item_source"
        ),
        sa.ForeignKeyConstraint(["draft_id"], ["calendar_drafts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_calendar_draft_items_draft_id"),
        "calendar_draft_items",
        ["draft_id"],
        unique=False,
    )

    op.create_table(
        "calendar_operations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("draft_id", sa.Integer(), nullable=False),
        sa.Column("item_id", sa.Integer(), nullable=False),
        sa.Column("operation_type", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("external_event_id", sa.String(length=500), nullable=True),
        sa.Column("external_etag", sa.String(length=500), nullable=True),
        sa.Column("error_category", sa.String(length=30), nullable=True),
        sa.Column("error_message", sa.String(length=1000), nullable=True),
        sa.Column("attempt", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "result_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'success', 'failed', 'unknown', 'skipped')",
            name="ck_calendar_operation_status",
        ),
        sa.ForeignKeyConstraint(["draft_id"], ["calendar_drafts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["item_id"], ["calendar_draft_items.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_calendar_operation_key"),
    )
    op.create_index(
        op.f("ix_calendar_operations_draft_id"),
        "calendar_operations",
        ["draft_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_calendar_operations_item_id"), "calendar_operations", ["item_id"], unique=False
    )
    op.create_index(
        op.f("ix_calendar_operations_workspace_id"),
        "calendar_operations",
        ["workspace_id"],
        unique=False,
    )

    op.create_table(
        "calendar_event_links",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("connection_id", sa.Integer(), nullable=False),
        sa.Column("source_type", sa.String(length=20), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("external_event_id", sa.String(length=500), nullable=False),
        sa.Column("external_etag", sa.String(length=500), nullable=True),
        sa.Column("content_fingerprint", sa.String(length=64), nullable=False),
        sa.Column(
            "source_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("state", sa.String(length=20), server_default="active", nullable=False),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "source_type IN ('task', 'plan_block')", name="ck_calendar_link_source_type"
        ),
        sa.CheckConstraint(
            "state IN ('active', 'deleted', 'external_missing')", name="ck_calendar_link_state"
        ),
        sa.ForeignKeyConstraint(["connection_id"], ["calendar_connections.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "connection_id", "source_type", "source_id", name="uq_calendar_link_source"
        ),
    )
    op.create_index(
        op.f("ix_calendar_event_links_connection_id"),
        "calendar_event_links",
        ["connection_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_calendar_event_links_workspace_id"),
        "calendar_event_links",
        ["workspace_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_table("calendar_event_links")
    op.drop_table("calendar_operations")
    op.drop_table("calendar_draft_items")
    op.drop_table("calendar_drafts")
    op.drop_table("calendar_oauth_states")
    op.drop_table("calendar_connections")
