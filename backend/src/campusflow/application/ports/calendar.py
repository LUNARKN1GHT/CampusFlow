"""M9 外部日历端口：供应商适配器与持久化边界。

应用层只依赖这里的协议；Google 与本地模拟实现位于 infrastructure/calendar。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from campusflow.domain.calendar import (
    CalendarEventPayload,
    CalendarSourceType,
    ConnectionStatus,
    DraftStatus,
    OperationStatus,
    OperationType,
)


@dataclass(frozen=True)
class OAuthTokenSet:
    access_token: str
    refresh_token: str | None
    expires_at: datetime
    account_email: str | None
    scopes: str


@dataclass(frozen=True)
class ProviderEventResult:
    external_event_id: str
    etag: str | None
    html_link: str | None = None
    existed: bool = False


class CalendarProvider(Protocol):
    """外部日历供应商；所有方法只接收已解密的令牌。"""

    name: str

    def authorization_url(self, *, state: str, redirect_uri: str) -> str: ...

    def exchange_code(self, *, code: str, redirect_uri: str) -> OAuthTokenSet: ...

    def refresh(self, *, refresh_token: str) -> OAuthTokenSet: ...

    def revoke(self, *, token: str) -> None: ...

    def ensure_calendar(self, *, access_token: str, timezone: str) -> str: ...

    def create_event(
        self, *, access_token: str, calendar_id: str, event: CalendarEventPayload
    ) -> ProviderEventResult: ...

    def update_event(
        self,
        *,
        access_token: str,
        calendar_id: str,
        external_event_id: str,
        etag: str | None,
        event: CalendarEventPayload,
    ) -> ProviderEventResult: ...

    def delete_event(
        self,
        *,
        access_token: str,
        calendar_id: str,
        external_event_id: str,
        etag: str | None,
    ) -> None: ...

    def find_event(
        self, *, access_token: str, calendar_id: str, operation_key: str
    ) -> ProviderEventResult | None: ...


@dataclass
class CalendarConnectionData:
    id: int
    workspace_id: int
    provider: str
    account_email: str
    calendar_id: str
    status: ConnectionStatus
    scopes: str
    token_expires_at: datetime | None
    created_at: datetime
    updated_at: datetime
    revoked_at: datetime | None = None


@dataclass(frozen=True)
class CalendarCredentialsData:
    access_token_ciphertext: str | None
    refresh_token_ciphertext: str | None
    token_expires_at: datetime | None


@dataclass
class CalendarOAuthStateData:
    id: int
    workspace_id: int
    provider: str
    state_hash: str
    redirect_uri: str
    expires_at: datetime
    consumed_at: datetime | None
    created_at: datetime


@dataclass
class CalendarDraftItemData:
    id: int
    draft_id: int
    operation_type: OperationType
    source_type: CalendarSourceType
    source_id: int
    title: str
    starts_at: datetime
    ends_at: datetime
    timezone: str
    date_only: bool
    location: str | None
    target_calendar_id: str
    external_event_id: str | None
    external_etag: str | None
    item_fingerprint: str
    content_fingerprint: str
    position: int


@dataclass
class CalendarDraftData:
    id: int
    workspace_id: int
    connection_id: int
    status: DraftStatus
    version: int
    content_fingerprint: str
    confirmed_fingerprint: str | None
    confirmed_at: datetime | None
    expires_at: datetime | None
    excluded: list[dict]
    warnings: list[dict]
    note: str | None
    created_at: datetime
    updated_at: datetime
    items: list[CalendarDraftItemData] = field(default_factory=list)
    confirmed_item_ids: list[int] = field(default_factory=list)


@dataclass
class CalendarOperationData:
    id: int
    workspace_id: int
    draft_id: int
    item_id: int
    operation_type: OperationType
    status: OperationStatus
    idempotency_key: str
    external_event_id: str | None
    external_etag: str | None
    error_category: str | None
    error_message: str | None
    attempt: int
    result_payload: dict
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime


@dataclass
class CalendarEventLinkData:
    id: int
    workspace_id: int
    connection_id: int
    source_type: CalendarSourceType
    source_id: int
    external_event_id: str
    external_etag: str | None
    content_fingerprint: str
    source_snapshot: dict
    state: str
    last_synced_at: datetime | None
    created_at: datetime
    updated_at: datetime


class CalendarStore(Protocol):
    def find_connection(
        self, workspace_id: int, provider: str, account_email: str
    ) -> CalendarConnectionData | None: ...

    def create_connection(
        self,
        workspace_id: int,
        *,
        provider: str,
        account_email: str,
        calendar_id: str,
        scopes: str,
        access_token_ciphertext: str,
        refresh_token_ciphertext: str | None,
        token_expires_at: datetime,
    ) -> CalendarConnectionData: ...

    def update_connection_tokens(
        self,
        connection_id: int,
        *,
        access_token_ciphertext: str,
        refresh_token_ciphertext: str | None,
        token_expires_at: datetime,
        scopes: str,
    ) -> CalendarConnectionData | None: ...

    def update_connection_calendar(
        self, connection_id: int, *, calendar_id: str
    ) -> CalendarConnectionData | None: ...

    def set_connection_status(
        self, connection_id: int, status: ConnectionStatus
    ) -> CalendarConnectionData | None: ...

    def get_connection(self, connection_id: int) -> CalendarConnectionData | None: ...

    def get_connection_for_workspace(
        self, workspace_id: int, connection_id: int
    ) -> CalendarConnectionData | None: ...

    def list_connections(self, workspace_id: int) -> list[CalendarConnectionData]: ...

    def get_credentials(self, connection_id: int) -> CalendarCredentialsData | None: ...

    def create_oauth_state(
        self,
        workspace_id: int,
        *,
        provider: str,
        state_hash: str,
        redirect_uri: str,
        expires_at: datetime,
    ) -> CalendarOAuthStateData: ...

    def consume_oauth_state(
        self, workspace_id: int, state_hash: str, *, now: datetime
    ) -> CalendarOAuthStateData | None: ...

    def create_draft(
        self,
        workspace_id: int,
        *,
        connection_id: int,
        status: DraftStatus,
        version: int,
        content_fingerprint: str,
        excluded: list[dict],
        warnings: list[dict],
        note: str | None,
        items: list[dict],
    ) -> CalendarDraftData: ...

    def get_draft(self, draft_id: int) -> CalendarDraftData | None: ...

    def get_draft_for_workspace(
        self, workspace_id: int, draft_id: int
    ) -> CalendarDraftData | None: ...

    def lock_draft(self, draft_id: int) -> CalendarDraftData | None: ...

    def update_draft_confirmation(
        self,
        draft_id: int,
        *,
        status: DraftStatus,
        version: int,
        content_fingerprint: str,
        confirmed_fingerprint: str,
        confirmed_item_ids: list[int],
        confirmed_at: datetime,
        expires_at: datetime,
        note: str | None,
    ) -> CalendarDraftData | None: ...

    def update_draft_status(
        self, draft_id: int, status: DraftStatus
    ) -> CalendarDraftData | None: ...

    def list_draft_items(self, draft_id: int) -> list[CalendarDraftItemData]: ...

    def cancel_pending_drafts(self, connection_id: int) -> int: ...

    def create_operation(
        self,
        workspace_id: int,
        *,
        draft_id: int,
        item_id: int,
        operation_type: OperationType,
        idempotency_key: str,
        started_at: datetime,
    ) -> CalendarOperationData: ...

    def find_operation_by_key(self, idempotency_key: str) -> CalendarOperationData | None: ...

    def get_operation(self, operation_id: int) -> CalendarOperationData | None: ...

    def update_operation_result(
        self,
        operation_id: int,
        *,
        status: OperationStatus,
        external_event_id: str | None,
        external_etag: str | None,
        error_category: str | None,
        error_message: str | None,
        result_payload: dict,
        finished_at: datetime,
        increment_attempt: bool = False,
    ) -> CalendarOperationData | None: ...

    def list_operations(
        self, workspace_id: int, *, draft_id: int | None = None
    ) -> list[CalendarOperationData]: ...

    def get_active_link(
        self,
        connection_id: int,
        source_type: CalendarSourceType,
        source_id: int,
    ) -> CalendarEventLinkData | None: ...

    def list_links(self, connection_id: int) -> list[CalendarEventLinkData]: ...

    def upsert_link(
        self,
        workspace_id: int,
        *,
        connection_id: int,
        source_type: CalendarSourceType,
        source_id: int,
        external_event_id: str,
        external_etag: str | None,
        content_fingerprint: str,
        source_snapshot: dict,
        state: str = "active",
    ) -> CalendarEventLinkData: ...

    def mark_link_deleted(
        self, connection_id: int, source_type: CalendarSourceType, source_id: int
    ) -> None: ...

    def mark_link_external_missing(
        self, connection_id: int, source_type: CalendarSourceType, source_id: int
    ) -> None: ...

    def commit(self) -> None: ...

    def flush(self) -> None: ...
