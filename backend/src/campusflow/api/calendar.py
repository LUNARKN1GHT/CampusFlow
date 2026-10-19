"""M9 外部日历 HTTP 路由：连接、授权、草案、执行与同步差异。"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from campusflow.api.deps import (
    get_calendar_provider,
    get_calendar_store,
    get_current_workspace,
    get_repositories,
    get_settings,
    get_token_cipher,
)
from campusflow.application import calendar as use_cases
from campusflow.application.ports.calendar import CalendarProvider, CalendarStore
from campusflow.application.ports.repositories import Repositories, WorkspaceData
from campusflow.core.config import Settings
from campusflow.infrastructure.calendar.tokens import TokenCipher

router = APIRouter(tags=["calendar"])

CurrentWorkspace = Annotated[WorkspaceData, Depends(get_current_workspace)]
Repos = Annotated[Repositories, Depends(get_repositories)]
Store = Annotated[CalendarStore, Depends(get_calendar_store)]
Provider = Annotated[CalendarProvider, Depends(get_calendar_provider)]
Cipher = Annotated[TokenCipher, Depends(get_token_cipher)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


class CalendarConnectionOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    provider: str
    account_email: str
    calendar_id: str
    status: str
    scopes: str
    token_expires_at: datetime | None
    created_at: datetime
    updated_at: datetime
    revoked_at: datetime | None


class OAuthStartOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    authorization_url: str
    expires_at: datetime


class DraftSourceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["tasks", "plan"]
    task_ids: list[int] = Field(default_factory=list)
    plan_id: int | None = None


class DraftCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connection_id: int
    source: DraftSourceIn
    note: str | None = Field(default=None, max_length=1000)


class DraftConfirmIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content_fingerprint: str = Field(min_length=16, max_length=128)
    item_ids: list[int]
    note: str | None = Field(default=None, max_length=1000)


class DraftExecuteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmation_fingerprint: str = Field(min_length=16, max_length=128)


class DraftItemOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    operation_type: str
    source_type: str
    source_id: int
    title: str
    starts_at: datetime
    ends_at: datetime
    timezone: str
    date_only: bool
    location: str | None
    external_event_id: str | None
    item_fingerprint: str


class DraftOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    connection_id: int
    status: str
    version: int
    content_fingerprint: str
    confirmed_fingerprint: str | None
    confirmed_item_ids: list[int]
    confirmed_at: datetime | None
    expires_at: datetime | None
    excluded: list[dict]
    warnings: list[dict]
    note: str | None
    created_at: datetime
    updated_at: datetime
    items: list[DraftItemOut]


class OperationOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    draft_id: int
    item_id: int
    operation_type: str
    status: str
    external_event_id: str | None
    error_category: str | None
    error_message: str | None
    attempt: int
    result_payload: dict
    started_at: datetime | None
    finished_at: datetime | None


class ExecutionItemOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item: DraftItemOut
    operation: OperationOut | None


class ExecutionOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    draft: DraftOut
    items: list[ExecutionItemOut]
    success: int
    failed: int
    unknown: int


class DifferenceOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_type: str
    source_type: str
    source_id: int
    title: str
    starts_at: datetime
    ends_at: datetime
    timezone: str
    date_only: bool
    location: str | None
    external_event_id: str | None
    reason_code: str
    reason: str


class DifferencesOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connection: CalendarConnectionOut
    differences: list[DifferenceOut]
    can_create_draft: bool
    note: str


class DifferenceDraftIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connection_id: int
    note: str | None = Field(default=None, max_length=1000)


@router.get("/calendar/connections", response_model=list[CalendarConnectionOut])
def list_connections(workspace: CurrentWorkspace, store: Store) -> list[CalendarConnectionOut]:
    return [_connection_out(item) for item in use_cases.list_connections(store, workspace.id)]


@router.get("/calendar/oauth/start", response_model=OAuthStartOut)
def start_oauth(
    workspace: CurrentWorkspace,
    store: Store,
    provider: Provider,
    settings: SettingsDep,
) -> OAuthStartOut:
    url, expires_at = use_cases.start_authorization(
        store,
        provider,
        workspace.id,
        redirect_uri=settings.google_calendar_redirect_uri,
    )
    return OAuthStartOut(authorization_url=url, expires_at=expires_at)


@router.get("/calendar/oauth/callback", response_model=CalendarConnectionOut)
def oauth_callback(
    workspace: CurrentWorkspace,
    store: Store,
    provider: Provider,
    cipher: Cipher,
    state: Annotated[str, Query()],
    code: Annotated[str | None, Query()] = None,
    error: Annotated[str | None, Query()] = None,
) -> CalendarConnectionOut:
    connection = use_cases.complete_authorization(
        store,
        provider,
        cipher,
        workspace,
        state=state,
        code=code,
        error=error,
    )
    return _connection_out(connection)


@router.delete("/calendar/connections/{connection_id}", response_model=CalendarConnectionOut)
def revoke_connection(
    connection_id: int,
    workspace: CurrentWorkspace,
    store: Store,
    provider: Provider,
    cipher: Cipher,
) -> CalendarConnectionOut:
    connection, _ = use_cases.revoke_connection(
        store, provider, cipher, workspace.id, connection_id
    )
    return _connection_out(connection)


@router.post("/calendar/drafts", response_model=DraftOut, status_code=201)
def create_draft(
    payload: DraftCreateIn,
    workspace: CurrentWorkspace,
    store: Store,
    repos: Repos,
) -> DraftOut:
    draft = use_cases.create_draft(
        store,
        repos,
        workspace,
        connection_id=payload.connection_id,
        source=use_cases.DraftSource(
            kind=payload.source.kind,
            task_ids=tuple(payload.source.task_ids),
            plan_id=payload.source.plan_id,
        ),
        note=payload.note,
    )
    return _draft_out(draft)


@router.get("/calendar/drafts/{draft_id}", response_model=DraftOut)
def get_draft(draft_id: int, workspace: CurrentWorkspace, store: Store) -> DraftOut:
    return _draft_out(use_cases.get_draft(store, workspace.id, draft_id))


@router.post("/calendar/drafts/{draft_id}/confirm", response_model=DraftOut)
def confirm_draft(
    draft_id: int,
    payload: DraftConfirmIn,
    workspace: CurrentWorkspace,
    store: Store,
    repos: Repos,
) -> DraftOut:
    draft = use_cases.confirm_draft(
        store,
        repos,
        workspace,
        draft_id,
        content_fingerprint_value=payload.content_fingerprint,
        item_ids=payload.item_ids,
        note=payload.note,
    )
    return _draft_out(draft)


@router.post("/calendar/drafts/{draft_id}/execute", response_model=ExecutionOut)
def execute_draft(
    draft_id: int,
    payload: DraftExecuteIn,
    workspace: CurrentWorkspace,
    store: Store,
    repos: Repos,
    provider: Provider,
    cipher: Cipher,
) -> ExecutionOut:
    draft, items = use_cases.execute_draft(
        store,
        repos,
        provider,
        cipher,
        workspace,
        draft_id,
        confirmation_fingerprint_value=payload.confirmation_fingerprint,
    )
    return _execution_out(store, workspace.id, draft, items)


@router.post("/calendar/drafts/{draft_id}/retry", response_model=ExecutionOut)
def retry_draft(
    draft_id: int,
    payload: DraftExecuteIn,
    workspace: CurrentWorkspace,
    store: Store,
    repos: Repos,
    provider: Provider,
    cipher: Cipher,
) -> ExecutionOut:
    draft, items = use_cases.retry_draft(
        store,
        repos,
        provider,
        cipher,
        workspace,
        draft_id,
        confirmation_fingerprint_value=payload.confirmation_fingerprint,
    )
    return _execution_out(store, workspace.id, draft, items)


@router.get("/calendar/operations", response_model=list[OperationOut])
def list_operations(
    workspace: CurrentWorkspace,
    store: Store,
    draft_id: Annotated[int | None, Query()] = None,
) -> list[OperationOut]:
    return [
        _operation_out(item)
        for item in use_cases.list_operations(store, workspace.id, draft_id=draft_id)
    ]


@router.post("/calendar/operations/{operation_id}/reconcile", response_model=OperationOut)
def reconcile_operation(
    operation_id: int,
    workspace: CurrentWorkspace,
    store: Store,
    provider: Provider,
    cipher: Cipher,
) -> OperationOut:
    operation = use_cases.reconcile_operation(store, provider, cipher, workspace.id, operation_id)
    return _operation_out(operation)


@router.get("/calendar/differences", response_model=DifferencesOut)
def list_differences(
    connection_id: Annotated[int, Query(ge=1)],
    workspace: CurrentWorkspace,
    store: Store,
    repos: Repos,
) -> DifferencesOut:
    connection, differences = use_cases.list_differences(store, repos, workspace, connection_id)
    note = (
        "连接已撤销或过期：只展示内部变化，不会执行外部操作。"
        if connection.status != "active"
        else "以下外部变更需要重新确认；确认前不会写入。"
    )
    return DifferencesOut(
        connection=_connection_out(connection),
        differences=[_difference_out(item) for item in differences],
        can_create_draft=connection.status == "active",
        note=note,
    )


@router.post("/calendar/differences/draft", response_model=DraftOut, status_code=201)
def create_difference_draft(
    payload: DifferenceDraftIn,
    workspace: CurrentWorkspace,
    store: Store,
    repos: Repos,
) -> DraftOut:
    draft = use_cases.create_difference_draft(
        store,
        repos,
        workspace,
        connection_id=payload.connection_id,
        note=payload.note,
    )
    return _draft_out(draft)


def _connection_out(item) -> CalendarConnectionOut:
    return CalendarConnectionOut(
        id=item.id,
        provider=item.provider,
        account_email=item.account_email,
        calendar_id=item.calendar_id,
        status=item.status.value,
        scopes=item.scopes,
        token_expires_at=item.token_expires_at,
        created_at=item.created_at,
        updated_at=item.updated_at,
        revoked_at=item.revoked_at,
    )


def _draft_out(item) -> DraftOut:
    return DraftOut(
        id=item.id,
        connection_id=item.connection_id,
        status=item.status.value,
        version=item.version,
        content_fingerprint=item.content_fingerprint,
        confirmed_fingerprint=item.confirmed_fingerprint,
        confirmed_item_ids=item.confirmed_item_ids,
        confirmed_at=item.confirmed_at,
        expires_at=item.expires_at,
        excluded=item.excluded,
        warnings=item.warnings,
        note=item.note,
        created_at=item.created_at,
        updated_at=item.updated_at,
        items=[_draft_item_out(row) for row in item.items],
    )


def _draft_item_out(item) -> DraftItemOut:
    return DraftItemOut(
        id=item.id,
        operation_type=item.operation_type.value,
        source_type=item.source_type.value,
        source_id=item.source_id,
        title=item.title,
        starts_at=item.starts_at,
        ends_at=item.ends_at,
        timezone=item.timezone,
        date_only=item.date_only,
        location=item.location,
        external_event_id=item.external_event_id,
        item_fingerprint=item.item_fingerprint,
    )


def _operation_out(item) -> OperationOut:
    return OperationOut(
        id=item.id,
        draft_id=item.draft_id,
        item_id=item.item_id,
        operation_type=item.operation_type.value,
        status=item.status.value,
        external_event_id=item.external_event_id,
        error_category=item.error_category,
        error_message=item.error_message,
        attempt=item.attempt,
        result_payload=item.result_payload,
        started_at=item.started_at,
        finished_at=item.finished_at,
    )


def _execution_out(store: CalendarStore, workspace_id: int, draft, items) -> ExecutionOut:
    operations = {
        item.item_id: item for item in store.list_operations(workspace_id, draft_id=draft.id)
    }
    rows = [
        ExecutionItemOut(
            item=_draft_item_out(item),
            operation=_operation_out(operations[item.id]) if item.id in operations else None,
        )
        for item in items
    ]
    success = sum(1 for row in rows if row.operation and row.operation.status == "success")
    failed = sum(1 for row in rows if row.operation and row.operation.status == "failed")
    unknown = sum(1 for row in rows if row.operation and row.operation.status == "unknown")
    return ExecutionOut(
        draft=_draft_out(draft),
        items=rows,
        success=success,
        failed=failed,
        unknown=unknown,
    )


def _difference_out(item) -> DifferenceOut:
    return DifferenceOut(
        operation_type=item.operation_type.value,
        source_type=item.source_type.value,
        source_id=item.source_id,
        title=item.title,
        starts_at=item.starts_at,
        ends_at=item.ends_at,
        timezone=item.timezone,
        date_only=item.date_only,
        location=item.location,
        external_event_id=item.external_event_id,
        reason_code=item.reason_code,
        reason=item.reason,
    )
