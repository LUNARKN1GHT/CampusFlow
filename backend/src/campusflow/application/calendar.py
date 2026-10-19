"""M9 外部日历用例：连接授权、草案确认、逐项执行与同步差异。

所有外部写入都由用户显式选择并确认；查询和预览不产生写入。外部失败不修改内部
任务或计划，只有实际成功的结果才写入外部事件映射。
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from hashlib import sha256
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from campusflow.application.ports.calendar import (
    CalendarConnectionData,
    CalendarDraftData,
    CalendarDraftItemData,
    CalendarEventLinkData,
    CalendarOperationData,
    CalendarProvider,
    CalendarStore,
    OAuthTokenSet,
    ProviderEventResult,
)
from campusflow.application.ports.repositories import Repositories, TaskData, WorkspaceData
from campusflow.domain.calendar import (
    CONFIRMATION_TTL,
    ERROR_MESSAGES,
    OAUTH_STATE_TTL,
    TOKEN_REFRESH_MARGIN,
    CalendarErrorCategory,
    CalendarEventPayload,
    CalendarProviderError,
    CalendarSourceType,
    CalendarUnknownResult,
    ConnectionStatus,
    DraftStatus,
    OperationStatus,
    OperationType,
    confirmation_fingerprint,
    confirmation_is_valid,
    content_fingerprint,
    draft_status_after,
    item_fingerprint,
    operation_key,
    source_content_fingerprint,
    utc_now,
)
from campusflow.domain.errors import ConflictError, DomainError, NotFoundError
from campusflow.domain.states import TaskProgress
from campusflow.infrastructure.calendar.tokens import TokenCipher

DUE_DISPLAY_MINUTES = 15


@dataclass(frozen=True)
class DraftSource:
    kind: str
    task_ids: tuple[int, ...] = ()
    plan_id: int | None = None


@dataclass(frozen=True)
class DraftItemBuild:
    operation_type: OperationType
    source_type: CalendarSourceType
    source_id: int
    payload: CalendarEventPayload
    external_event_id: str | None
    external_etag: str | None
    item_fingerprint: str
    content_fingerprint: str
    snapshot: dict


@dataclass(frozen=True)
class DraftBuildResult:
    items: list[DraftItemBuild]
    excluded: list[dict]
    warnings: list[dict]


@dataclass(frozen=True)
class SyncDifference:
    operation_type: OperationType
    source_type: CalendarSourceType
    source_id: int
    title: str
    starts_at: datetime
    ends_at: datetime
    timezone: str
    date_only: bool
    location: str | None
    external_event_id: str | None
    external_etag: str | None
    reason_code: str
    reason: str
    snapshot: dict


# ---- 连接与授权 -----------------------------------------------------------


def list_connections(store: CalendarStore, workspace_id: int) -> list[CalendarConnectionData]:
    return store.list_connections(workspace_id)


def start_authorization(
    store: CalendarStore,
    provider: CalendarProvider,
    workspace_id: int,
    *,
    redirect_uri: str,
) -> tuple[str, datetime]:
    state = secrets.token_urlsafe(32)
    now = utc_now()
    expires_at = now + OAUTH_STATE_TTL
    store.create_oauth_state(
        workspace_id,
        provider=provider.name,
        state_hash=_state_hash(state),
        redirect_uri=redirect_uri,
        expires_at=expires_at,
    )
    store.commit()
    try:
        url = provider.authorization_url(state=state, redirect_uri=redirect_uri)
    except CalendarProviderError as exc:
        raise DomainError(ERROR_MESSAGES[exc.category]) from exc
    return url, expires_at


def complete_authorization(
    store: CalendarStore,
    provider: CalendarProvider,
    cipher: TokenCipher,
    workspace: WorkspaceData,
    *,
    state: str,
    code: str | None,
    error: str | None,
) -> CalendarConnectionData:
    if not state:
        raise DomainError("授权回调缺少 state，未建立日历连接。")
    stored = store.consume_oauth_state(workspace.id, _state_hash(state), now=utc_now())
    if stored is None:
        raise DomainError("授权状态已过期或已使用，请重新发起连接。")
    if error:
        store.commit()
        raise DomainError("授权未完成，未建立日历连接。")
    if not code:
        store.commit()
        raise DomainError("授权回调缺少授权码，未建立日历连接。")
    try:
        tokens = provider.exchange_code(code=code, redirect_uri=stored.redirect_uri)
    except CalendarProviderError as exc:
        store.commit()
        raise DomainError(ERROR_MESSAGES[exc.category]) from exc
    if "calendar.app.created" not in tokens.scopes:
        store.commit()
        raise DomainError("授权范围不足，未建立日历连接。")
    account_email = tokens.account_email or "unknown@calendar"
    access_ciphertext = cipher.encrypt(tokens.access_token)
    refresh_ciphertext = cipher.encrypt(tokens.refresh_token) if tokens.refresh_token else None
    existing = store.find_connection(workspace.id, provider.name, account_email)
    if existing is None:
        try:
            calendar_id = provider.ensure_calendar(
                access_token=tokens.access_token, timezone=workspace.timezone
            )
        except CalendarProviderError as exc:
            store.commit()
            raise DomainError(ERROR_MESSAGES[exc.category]) from exc
        connection = store.create_connection(
            workspace.id,
            provider=provider.name,
            account_email=account_email,
            calendar_id=calendar_id,
            scopes=tokens.scopes,
            access_token_ciphertext=access_ciphertext,
            refresh_token_ciphertext=refresh_ciphertext,
            token_expires_at=tokens.expires_at,
        )
    else:
        preserved_refresh = refresh_ciphertext or _existing_refresh(store, existing.id)
        connection = store.update_connection_tokens(
            existing.id,
            access_token_ciphertext=access_ciphertext,
            refresh_token_ciphertext=preserved_refresh,
            token_expires_at=tokens.expires_at,
            scopes=tokens.scopes,
        )
    store.commit()
    if connection is None:
        raise DomainError("日历连接保存失败，请重试。")
    return connection


def revoke_connection(
    store: CalendarStore,
    provider: CalendarProvider,
    cipher: TokenCipher,
    workspace_id: int,
    connection_id: int,
) -> tuple[CalendarConnectionData, int]:
    connection = _require_connection(store, workspace_id, connection_id)
    credentials = store.get_credentials(connection.id)
    if credentials is not None:
        token = cipher.decrypt(credentials.refresh_token_ciphertext) or cipher.decrypt(
            credentials.access_token_ciphertext
        )
        if token:
            try:
                provider.revoke(token=token)
            except Exception:
                # 撤销外部令牌失败不阻止本地断开；本地不再执行任何待写入操作。
                pass
    updated = store.set_connection_status(connection.id, ConnectionStatus.REVOKED)
    cancelled = store.cancel_pending_drafts(connection.id)
    store.commit()
    if updated is None:
        raise NotFoundError("日历连接不存在")
    return updated, cancelled


# ---- 草案与确认 -----------------------------------------------------------


def create_draft(
    store: CalendarStore,
    repos: Repositories,
    workspace: WorkspaceData,
    *,
    connection_id: int,
    source: DraftSource,
    note: str | None = None,
) -> CalendarDraftData:
    connection = _require_active_connection(store, workspace.id, connection_id)
    if source.kind == "tasks":
        built = _build_task_items(store, repos, workspace, connection, source.task_ids)
    elif source.kind == "plan":
        if source.plan_id is None:
            raise DomainError("计划模式需要提供 plan_id。")
        built = _build_plan_items(store, repos, workspace, connection, source.plan_id)
    else:
        raise DomainError("不支持的日历草案来源。")
    return _persist_draft(store, workspace, connection, built, note)


def get_draft(store: CalendarStore, workspace_id: int, draft_id: int) -> CalendarDraftData:
    return _require_draft(store, workspace_id, draft_id)


def confirm_draft(
    store: CalendarStore,
    repos: Repositories,
    workspace: WorkspaceData,
    draft_id: int,
    *,
    content_fingerprint_value: str,
    item_ids: list[int],
    note: str | None,
) -> CalendarDraftData:
    draft = _require_draft(store, workspace.id, draft_id)
    if draft.status == DraftStatus.COMPLETED:
        raise ConflictError("该草案已完成，不能再次确认。")
    if draft.status == DraftStatus.CANCELLED:
        raise ConflictError("该草案已取消，请重新生成。")
    _validate_items_are_current(repos, workspace, draft)
    current = _current_content_fingerprint(draft)
    if content_fingerprint_value != current or draft.content_fingerprint != current:
        raise ConflictError("草案内容已变化，请重新预览后再确认。")
    selected = _validate_item_selection(draft, item_ids)
    now = utc_now()
    version = draft.version if draft.status == DraftStatus.DRAFT else draft.version + 1
    confirmation = confirmation_fingerprint(
        content_fingerprint=current,
        item_ids=[item.id for item in selected],
        version=version,
    )
    updated = store.update_draft_confirmation(
        draft.id,
        status=DraftStatus.CONFIRMED,
        version=version,
        content_fingerprint=current,
        confirmed_fingerprint=confirmation,
        confirmed_item_ids=[item.id for item in selected],
        confirmed_at=now,
        expires_at=now + CONFIRMATION_TTL,
        note=note,
    )
    store.commit()
    if updated is None:
        raise NotFoundError("日历草案不存在")
    return updated


def execute_draft(
    store: CalendarStore,
    repos: Repositories,
    provider: CalendarProvider,
    cipher: TokenCipher,
    workspace: WorkspaceData,
    draft_id: int,
    *,
    confirmation_fingerprint_value: str,
) -> tuple[CalendarDraftData, list[CalendarDraftItemData]]:
    draft = _require_draft(store, workspace.id, draft_id)
    locked = store.lock_draft(draft.id)
    if locked is None:
        raise NotFoundError("日历草案不存在")
    draft = locked
    connection = _require_active_connection(store, workspace.id, draft.connection_id)
    if draft.status == DraftStatus.COMPLETED:
        return draft, _selected_items(draft)
    _validate_items_are_current(repos, workspace, draft)
    _validate_confirmation(
        store, draft, connection, confirmation_fingerprint_value, persist_expiry=True
    )
    return _execute_items(
        store, provider, cipher, workspace, draft, connection, _selected_items(draft)
    )


def retry_draft(
    store: CalendarStore,
    repos: Repositories,
    provider: CalendarProvider,
    cipher: TokenCipher,
    workspace: WorkspaceData,
    draft_id: int,
    *,
    confirmation_fingerprint_value: str,
) -> tuple[CalendarDraftData, list[CalendarDraftItemData]]:
    draft = _require_draft(store, workspace.id, draft_id)
    locked = store.lock_draft(draft.id)
    if locked is None:
        raise NotFoundError("日历草案不存在")
    draft = locked
    connection = _require_active_connection(store, workspace.id, draft.connection_id)
    _validate_items_are_current(repos, workspace, draft)
    _validate_confirmation(
        store, draft, connection, confirmation_fingerprint_value, persist_expiry=True
    )
    selected = _selected_items(draft)
    operations = {
        operation.item_id: operation
        for operation in store.list_operations(workspace.id, draft_id=draft.id)
    }
    retryable = [
        item
        for item in selected
        if item.id not in operations
        or operations[item.id].status
        in {OperationStatus.FAILED, OperationStatus.UNKNOWN, OperationStatus.PENDING}
    ]
    if not retryable:
        raise ConflictError("没有可重试的失败项；已成功的操作不会重复执行。")
    updated, _ = _execute_items(store, provider, cipher, workspace, draft, connection, retryable)
    return updated, _selected_items(updated)


# ---- 执行与对账 -----------------------------------------------------------


def _execute_items(
    store: CalendarStore,
    provider: CalendarProvider,
    cipher: TokenCipher,
    workspace: WorkspaceData,
    draft: CalendarDraftData,
    connection: CalendarConnectionData,
    items: list[CalendarDraftItemData],
) -> tuple[CalendarDraftData, list[CalendarDraftItemData]]:
    if not items:
        raise ConflictError("没有选中的日历操作。")
    store.update_draft_status(draft.id, DraftStatus.EXECUTING)
    store.flush()
    try:
        access_token = _access_token(store, provider, cipher, connection)
    except DomainError:
        store.commit()
        raise
    auth_failed = False
    for item in items:
        key = operation_key(
            draft_id=draft.id, item_id=item.id, item_fingerprint=item.item_fingerprint
        )
        operation = store.find_operation_by_key(key)
        if operation is not None and operation.status == OperationStatus.SUCCESS:
            continue
        if operation is None:
            operation = store.create_operation(
                workspace.id,
                draft_id=draft.id,
                item_id=item.id,
                operation_type=item.operation_type,
                idempotency_key=key,
                started_at=utc_now(),
            )
        is_retry = operation.status != OperationStatus.PENDING
        if auth_failed:
            store.update_operation_result(
                operation.id,
                status=OperationStatus.FAILED,
                external_event_id=operation.external_event_id,
                external_etag=operation.external_etag,
                error_category=CalendarErrorCategory.AUTH.value,
                error_message=ERROR_MESSAGES[CalendarErrorCategory.AUTH],
                result_payload={},
                finished_at=utc_now(),
                increment_attempt=is_retry,
            )
            continue
        payload = _payload_from_item(item, operation_key_value=key)
        try:
            result = _call_provider(
                store, provider, access_token, connection, item, payload, operation.status
            )
        except CalendarUnknownResult as exc:
            store.update_operation_result(
                operation.id,
                status=OperationStatus.UNKNOWN,
                external_event_id=None,
                external_etag=None,
                error_category=CalendarErrorCategory.UNKNOWN.value,
                error_message=ERROR_MESSAGES[CalendarErrorCategory.UNKNOWN],
                result_payload={"detail": str(exc)[:200]},
                finished_at=utc_now(),
                increment_attempt=is_retry,
            )
            continue
        except CalendarProviderError as exc:
            if exc.category == CalendarErrorCategory.AUTH:
                store.set_connection_status(connection.id, ConnectionStatus.EXPIRED)
                auth_failed = True
            if item.operation_type == OperationType.DELETE and exc.category == (
                CalendarErrorCategory.NOT_FOUND
            ):
                _mark_delete_success(
                    store, connection, item, operation.id, increment_attempt=is_retry
                )
                continue
            if item.operation_type == OperationType.UPDATE and exc.category == (
                CalendarErrorCategory.NOT_FOUND
            ):
                store.mark_link_external_missing(connection.id, item.source_type, item.source_id)
            store.update_operation_result(
                operation.id,
                status=OperationStatus.FAILED,
                external_event_id=operation.external_event_id,
                external_etag=operation.external_etag,
                error_category=exc.category.value,
                error_message=ERROR_MESSAGES[exc.category],
                result_payload={"detail": exc.detail[:200]} if exc.detail else {},
                finished_at=utc_now(),
                increment_attempt=is_retry,
            )
            continue
        _mark_operation_success(
            store, connection, item, operation.id, result, increment_attempt=is_retry
        )
    operations = store.list_operations(workspace.id, draft_id=draft.id)
    selected_ids = {item.id for item in _selected_items(draft)}
    selected_ops = [operation for operation in operations if operation.item_id in selected_ids]
    success = sum(1 for op in selected_ops if op.status == OperationStatus.SUCCESS)
    failed = sum(1 for op in selected_ops if op.status == OperationStatus.FAILED)
    unknown = sum(1 for op in selected_ops if op.status == OperationStatus.UNKNOWN)
    status = draft_status_after(
        total=len(selected_ids), success=success, failed=failed, unknown=unknown
    )
    updated = store.update_draft_status(draft.id, status)
    store.commit()
    if updated is None:
        raise NotFoundError("日历草案不存在")
    return updated, items


def _call_provider(
    store: CalendarStore,
    provider: CalendarProvider,
    access_token: str,
    connection: CalendarConnectionData,
    item: CalendarDraftItemData,
    payload: CalendarEventPayload,
    previous_status: OperationStatus,
) -> ProviderEventResult | None:
    if item.operation_type == OperationType.CREATE:
        existing = provider.find_event(
            access_token=access_token,
            calendar_id=connection.calendar_id,
            operation_key=payload.operation_key,
        )
        if existing is not None:
            return existing
        return provider.create_event(
            access_token=access_token,
            calendar_id=connection.calendar_id,
            event=payload,
        )
    if item.operation_type == OperationType.UPDATE:
        if previous_status == OperationStatus.UNKNOWN:
            existing = provider.find_event(
                access_token=access_token,
                calendar_id=connection.calendar_id,
                operation_key=payload.operation_key,
            )
            if existing is not None and existing.external_event_id == item.external_event_id:
                return existing
        if not item.external_event_id:
            raise CalendarProviderError(CalendarErrorCategory.NOT_FOUND)
        return provider.update_event(
            access_token=access_token,
            calendar_id=connection.calendar_id,
            external_event_id=item.external_event_id,
            etag=item.external_etag,
            event=payload,
        )
    if previous_status == OperationStatus.UNKNOWN:
        existing = provider.find_event(
            access_token=access_token,
            calendar_id=connection.calendar_id,
            operation_key=payload.operation_key,
        )
        if existing is None:
            return None
    if not item.external_event_id:
        raise CalendarProviderError(CalendarErrorCategory.NOT_FOUND)
    provider.delete_event(
        access_token=access_token,
        calendar_id=connection.calendar_id,
        external_event_id=item.external_event_id,
        etag=item.external_etag,
    )
    return None


def _mark_operation_success(
    store: CalendarStore,
    connection: CalendarConnectionData,
    item: CalendarDraftItemData,
    operation_id: int,
    result: ProviderEventResult | None,
    *,
    increment_attempt: bool = False,
) -> None:
    if item.operation_type == OperationType.DELETE:
        _mark_delete_success(
            store, connection, item, operation_id, increment_attempt=increment_attempt
        )
        return
    if result is None:
        raise DomainError("外部日历未返回事件标识")
    store.update_operation_result(
        operation_id,
        status=OperationStatus.SUCCESS,
        external_event_id=result.external_event_id,
        external_etag=result.etag,
        error_category=None,
        error_message=None,
        result_payload={
            "external_event_id": result.external_event_id,
            "etag": result.etag,
            "html_link": result.html_link,
            "reconciled": result.existed,
        },
        finished_at=utc_now(),
        increment_attempt=increment_attempt,
    )
    store.upsert_link(
        connection.workspace_id,
        connection_id=connection.id,
        source_type=item.source_type,
        source_id=item.source_id,
        external_event_id=result.external_event_id,
        external_etag=result.etag,
        content_fingerprint=item.content_fingerprint,
        source_snapshot=_snapshot(item.title, _payload_from_item(item, operation_key_value="")),
    )


def _mark_delete_success(
    store: CalendarStore,
    connection: CalendarConnectionData,
    item: CalendarDraftItemData,
    operation_id: int,
    *,
    increment_attempt: bool = False,
) -> None:
    store.update_operation_result(
        operation_id,
        status=OperationStatus.SUCCESS,
        external_event_id=item.external_event_id,
        external_etag=None,
        error_category=None,
        error_message=None,
        result_payload={"external_state": "not_found", "deleted": True},
        finished_at=utc_now(),
        increment_attempt=increment_attempt,
    )
    store.mark_link_deleted(connection.id, item.source_type, item.source_id)


def reconcile_operation(
    store: CalendarStore,
    provider: CalendarProvider,
    cipher: TokenCipher,
    workspace_id: int,
    operation_id: int,
) -> CalendarOperationData:
    operation = store.get_operation(operation_id)
    if operation is None or operation.workspace_id != workspace_id:
        raise NotFoundError("外部操作记录不存在")
    draft = store.get_draft(operation.draft_id)
    if draft is None or draft.workspace_id != workspace_id:
        raise NotFoundError("外部操作记录不存在")
    connection = _require_active_connection(store, workspace_id, draft.connection_id)
    item = next((row for row in draft.items if row.id == operation.item_id), None)
    if item is None:
        raise NotFoundError("外部操作记录不存在")
    access_token = _access_token(store, provider, cipher, connection)
    try:
        existing = provider.find_event(
            access_token=access_token,
            calendar_id=connection.calendar_id,
            operation_key=operation.idempotency_key,
        )
    except (CalendarUnknownResult, CalendarProviderError):
        store.commit()
        return store.get_operation(operation.id) or operation
    if item.operation_type == OperationType.DELETE:
        if existing is None:
            _mark_delete_success(store, connection, item, operation.id)
        else:
            store.update_operation_result(
                operation.id,
                status=OperationStatus.FAILED,
                external_event_id=existing.external_event_id,
                external_etag=existing.etag,
                error_category=CalendarErrorCategory.TRANSIENT.value,
                error_message="外部事件仍存在，可以重试删除。",
                result_payload={"external_state": "present"},
                finished_at=utc_now(),
            )
    elif existing is not None:
        _mark_operation_success(store, connection, item, operation.id, existing)
    else:
        if item.operation_type == OperationType.UPDATE:
            store.mark_link_external_missing(connection.id, item.source_type, item.source_id)
        store.update_operation_result(
            operation.id,
            status=OperationStatus.FAILED,
            external_event_id=None,
            external_etag=None,
            error_category=CalendarErrorCategory.NOT_FOUND.value,
            error_message="外部日历中没有找到该操作对应的事件，可以安全重试。",
            result_payload={"external_state": "absent"},
            finished_at=utc_now(),
        )
    store.commit()
    updated = store.get_operation(operation.id)
    if updated is None:
        raise NotFoundError("外部操作记录不存在")
    return updated


def list_operations(
    store: CalendarStore, workspace_id: int, *, draft_id: int | None = None
) -> list[CalendarOperationData]:
    return store.list_operations(workspace_id, draft_id=draft_id)


# ---- 通知与计划变化引起的同步差异 -----------------------------------------


def list_differences(
    store: CalendarStore,
    repos: Repositories,
    workspace: WorkspaceData,
    connection_id: int,
) -> tuple[CalendarConnectionData, list[SyncDifference]]:
    connection = _require_connection(store, workspace.id, connection_id)
    differences: list[SyncDifference] = []
    for link in store.list_links(connection.id):
        if link.state != "active":
            continue
        difference = _difference_for_link(repos, workspace, link)
        if difference is not None:
            differences.append(difference)
    differences.sort(key=lambda item: (item.source_type.value, item.source_id))
    return connection, differences


def create_difference_draft(
    store: CalendarStore,
    repos: Repositories,
    workspace: WorkspaceData,
    *,
    connection_id: int,
    note: str | None = None,
) -> CalendarDraftData:
    connection = _require_active_connection(store, workspace.id, connection_id)
    _, differences = list_differences(store, repos, workspace, connection.id)
    if not differences:
        raise DomainError("没有需要同步的外部变更。")
    items: list[DraftItemBuild] = []
    warnings: list[dict] = []
    for difference in differences:
        payload = CalendarEventPayload(
            title=difference.title,
            timezone=difference.timezone,
            date_only=difference.date_only,
            starts_at=difference.starts_at,
            ends_at=difference.ends_at,
            location=difference.location,
            operation_key="",
        )
        items.append(
            DraftItemBuild(
                operation_type=difference.operation_type,
                source_type=difference.source_type,
                source_id=difference.source_id,
                payload=payload,
                external_event_id=difference.external_event_id,
                external_etag=difference.external_etag,
                item_fingerprint=item_fingerprint(
                    operation_type=difference.operation_type,
                    source_type=difference.source_type,
                    source_id=difference.source_id,
                    payload=payload,
                    external_event_id=difference.external_event_id,
                ),
                content_fingerprint=source_content_fingerprint(
                    source_type=difference.source_type,
                    source_id=difference.source_id,
                    payload=payload,
                ),
                snapshot=difference.snapshot,
            )
        )
        warnings.append(
            {
                "code": difference.reason_code,
                "source_type": difference.source_type.value,
                "source_id": difference.source_id,
                "message": difference.reason,
            }
        )
    built = DraftBuildResult(items=items, excluded=[], warnings=warnings)
    return _persist_draft(store, workspace, connection, built, note)


# ---- 内部构建 -------------------------------------------------------------


def _build_task_items(
    store: CalendarStore,
    repos: Repositories,
    workspace: WorkspaceData,
    connection: CalendarConnectionData,
    task_ids: tuple[int, ...],
) -> DraftBuildResult:
    if not task_ids:
        raise DomainError("请至少选择一个任务。")
    tasks: list[TaskData] = []
    seen: set[int] = set()
    for task_id in task_ids:
        if task_id in seen:
            continue
        seen.add(task_id)
        task = repos.tasks.get(task_id)
        if task is None or task.workspace_id != workspace.id:
            raise NotFoundError(f"任务 {task_id} 不存在")
        tasks.append(task)
    items: list[DraftItemBuild] = []
    excluded: list[dict] = []
    warnings: list[dict] = []
    timed = False
    for task in tasks:
        if task.progress == TaskProgress.CANCELLED:
            excluded.append(_excluded("task", task.id, task.title, "cancelled", "任务已取消"))
            continue
        if task.due_date is None:
            excluded.append(_excluded("task", task.id, task.title, "no_due_date", "缺少截止日期"))
            continue
        payload = _task_payload(task, workspace.timezone)
        timed = timed or not payload.date_only
        built = _item_for_source(
            store,
            connection,
            CalendarSourceType.TASK,
            task.id,
            task.title,
            payload,
            category="task",
        )
        if built is None:
            warnings.append(
                {
                    "code": "already_synced",
                    "source_type": "task",
                    "source_id": task.id,
                    "message": f"“{task.title}”已同步且内容未变化，本次不会重复写入。",
                }
            )
            continue
        items.append(built)
    if timed:
        warnings.append(
            {
                "code": "due_display_window",
                "message": "带时刻的截止按 15 分钟显示窗口写入，不改变任务原始截止时间。",
            }
        )
    return DraftBuildResult(items=items, excluded=excluded, warnings=warnings)


def _build_plan_items(
    store: CalendarStore,
    repos: Repositories,
    workspace: WorkspaceData,
    connection: CalendarConnectionData,
    plan_id: int,
) -> DraftBuildResult:
    plan = repos.plans.get_draft(plan_id)
    if plan is None or plan.workspace_id != workspace.id:
        raise NotFoundError("计划不存在")
    if plan.status != "accepted":
        raise DomainError("只能写入当前已接受计划，草案不会自动接受。")
    items: list[DraftItemBuild] = []
    excluded: list[dict] = []
    warnings: list[dict] = []
    for block in sorted(plan.blocks, key=lambda row: (row.starts_at, row.seq, row.id)):
        if block.kind == "buffer":
            continue
        if block.kind != "task" or block.task_id is None:
            excluded.append(
                _excluded("plan_block", block.id, block.title, "orphan", "时间块没有关联任务")
            )
            continue
        task = repos.tasks.get(block.task_id)
        if task is None or task.workspace_id != workspace.id:
            excluded.append(
                _excluded("plan_block", block.id, block.title, "orphan", "关联任务不存在")
            )
            continue
        if task.progress == TaskProgress.CANCELLED:
            excluded.append(
                _excluded("plan_block", block.id, block.title, "cancelled", "任务已取消")
            )
            continue
        payload = _plan_block_payload(block, workspace)
        built = _item_for_source(
            store,
            connection,
            CalendarSourceType.PLAN_BLOCK,
            block.id,
            block.title,
            payload,
            category="plan_block",
        )
        if built is None:
            warnings.append(
                {
                    "code": "already_synced",
                    "source_type": "plan_block",
                    "source_id": block.id,
                    "message": f"“{block.title}”的计划时间块已同步且未变化。",
                }
            )
            continue
        items.append(built)
    if not plan.blocks:
        warnings.append({"code": "empty_plan", "message": "当前计划没有可写入的时间块。"})
    return DraftBuildResult(items=items, excluded=excluded, warnings=warnings)


def _item_for_source(
    store: CalendarStore,
    connection: CalendarConnectionData,
    source_type: CalendarSourceType,
    source_id: int,
    title: str,
    payload: CalendarEventPayload,
    *,
    category: str,
) -> DraftItemBuild | None:
    link = store.get_active_link(connection.id, source_type, source_id)
    signature = source_content_fingerprint(
        source_type=source_type, source_id=source_id, payload=payload
    )
    if link is not None and link.content_fingerprint == signature:
        return None
    operation_type = OperationType.UPDATE if link is not None else OperationType.CREATE
    external_id = link.external_event_id if link is not None else None
    external_etag = link.external_etag if link is not None else None
    return DraftItemBuild(
        operation_type=operation_type,
        source_type=source_type,
        source_id=source_id,
        payload=payload,
        external_event_id=external_id,
        external_etag=external_etag,
        item_fingerprint=item_fingerprint(
            operation_type=operation_type,
            source_type=source_type,
            source_id=source_id,
            payload=payload,
            external_event_id=external_id,
        ),
        content_fingerprint=signature,
        snapshot=_snapshot(title, payload),
    )


def _persist_draft(
    store: CalendarStore,
    workspace: WorkspaceData,
    connection: CalendarConnectionData,
    built: DraftBuildResult,
    note: str | None,
) -> CalendarDraftData:
    if not built.items:
        reasons = "；".join(item["message"] for item in built.excluded[:3])
        raise DomainError(
            "没有可写入的日历事件。"
            + (f"未纳入原因：{reasons}。" if reasons else "请先选择有截止时间的任务或已接受计划。")
        )
    content = content_fingerprint(
        connection_id=connection.id,
        provider=_connection_provider(connection),
        calendar_id=connection.calendar_id,
        item_fingerprints=[item.item_fingerprint for item in built.items],
    )
    items = [
        {
            "operation_type": item.operation_type.value,
            "source_type": item.source_type.value,
            "source_id": item.source_id,
            "title": item.payload.title,
            "starts_at": item.payload.starts_at,
            "ends_at": item.payload.ends_at,
            "timezone": item.payload.timezone,
            "date_only": item.payload.date_only,
            "location": item.payload.location,
            "target_calendar_id": connection.calendar_id,
            "external_event_id": item.external_event_id,
            "external_etag": item.external_etag,
            "item_fingerprint": item.item_fingerprint,
            "content_fingerprint": item.content_fingerprint,
        }
        for item in built.items
    ]
    draft = store.create_draft(
        workspace.id,
        connection_id=connection.id,
        status=DraftStatus.DRAFT,
        version=1,
        content_fingerprint=content,
        excluded=built.excluded,
        warnings=built.warnings,
        note=note,
        items=items,
    )
    store.commit()
    return draft


def _difference_for_link(
    repos: Repositories, workspace: WorkspaceData, link: CalendarEventLinkData
) -> SyncDifference | None:
    snapshot = link.source_snapshot or {}
    if link.source_type == CalendarSourceType.TASK:
        task = repos.tasks.get(link.source_id)
        if task is None or task.workspace_id != workspace.id:
            return _delete_difference(link, snapshot, "source_deleted", "任务已被删除")
        if task.progress == TaskProgress.CANCELLED:
            return _delete_difference(link, snapshot, "task_cancelled", "任务已取消")
        if task.due_date is None:
            return _delete_difference(link, snapshot, "task_due_removed", "任务已没有截止日期")
        payload = _task_payload(task, workspace.timezone)
        if (
            source_content_fingerprint(
                source_type=link.source_type, source_id=link.source_id, payload=payload
            )
            == link.content_fingerprint
        ):
            return None
        return SyncDifference(
            operation_type=OperationType.UPDATE,
            source_type=link.source_type,
            source_id=link.source_id,
            title=payload.title,
            starts_at=payload.starts_at,
            ends_at=payload.ends_at,
            timezone=payload.timezone,
            date_only=payload.date_only,
            location=payload.location,
            external_event_id=link.external_event_id,
            external_etag=link.external_etag,
            reason_code="task_changed",
            reason="任务内容或截止时间发生变化，需要重新确认外部更新。",
            snapshot=_snapshot(payload.title, payload),
        )
    block = repos.plans.get_block(link.source_id)
    if block is None:
        return _delete_difference(link, snapshot, "source_deleted", "计划时间块已被删除")
    plan = repos.plans.get_draft(block.draft_id)
    if plan is None or plan.workspace_id != workspace.id or plan.status != "accepted":
        return _delete_difference(
            link, snapshot, "plan_superseded", "计划已被新版本替代，不再作为当前安排"
        )
    task = repos.tasks.get(block.task_id) if block.task_id is not None else None
    if task is None or task.workspace_id != workspace.id:
        return _delete_difference(link, snapshot, "source_deleted", "时间块关联任务已不存在")
    if task.progress == TaskProgress.CANCELLED:
        return _delete_difference(link, snapshot, "task_cancelled", "任务已取消")
    payload = _plan_block_payload(block, workspace)
    if (
        source_content_fingerprint(
            source_type=link.source_type, source_id=link.source_id, payload=payload
        )
        == link.content_fingerprint
    ):
        return None
    return SyncDifference(
        operation_type=OperationType.UPDATE,
        source_type=link.source_type,
        source_id=link.source_id,
        title=payload.title,
        starts_at=payload.starts_at,
        ends_at=payload.ends_at,
        timezone=payload.timezone,
        date_only=False,
        location=None,
        external_event_id=link.external_event_id,
        external_etag=link.external_etag,
        reason_code="plan_changed",
        reason="计划时间块发生变化，需要重新确认外部更新。",
        snapshot=_snapshot(payload.title, payload),
    )


def _delete_difference(
    link: CalendarEventLinkData, snapshot: dict, code: str, reason: str
) -> SyncDifference:
    return SyncDifference(
        operation_type=OperationType.DELETE,
        source_type=link.source_type,
        source_id=link.source_id,
        title=str(snapshot.get("title") or "已删除的安排"),
        starts_at=datetime.fromisoformat(snapshot["starts_at"]),
        ends_at=datetime.fromisoformat(snapshot["ends_at"]),
        timezone=str(snapshot.get("timezone") or "UTC"),
        date_only=bool(snapshot.get("date_only", False)),
        location=snapshot.get("location"),
        external_event_id=link.external_event_id,
        external_etag=link.external_etag,
        reason_code=code,
        reason=reason,
        snapshot=snapshot,
    )


# ---- 校验与令牌 -----------------------------------------------------------


def _validate_confirmation(
    store: CalendarStore,
    draft: CalendarDraftData,
    connection: CalendarConnectionData,
    confirmation: str,
    *,
    persist_expiry: bool,
) -> None:
    current = _current_content_fingerprint(draft)
    if draft.content_fingerprint != current:
        raise ConflictError("草案内容已变化，请重新预览并确认。")
    if any(item.target_calendar_id != connection.calendar_id for item in draft.items):
        raise ConflictError("目标日历或账户已变化，请重新生成草案。")
    if not draft.confirmed_item_ids:
        raise ConflictError("草案尚未确认。")
    expected = confirmation_fingerprint(
        content_fingerprint=current,
        item_ids=draft.confirmed_item_ids,
        version=draft.version,
    )
    if confirmation != expected or draft.confirmed_fingerprint != expected:
        raise ConflictError("确认内容与当前草案不一致，请重新确认。")
    now = utc_now()
    if not confirmation_is_valid(
        status=draft.status.value,
        confirmed_fingerprint=draft.confirmed_fingerprint,
        expires_at=draft.expires_at,
        expected_fingerprint=expected,
        now=now,
    ):
        if persist_expiry and draft.expires_at is not None and _as_utc(draft.expires_at) <= now:
            store.update_draft_status(draft.id, DraftStatus.EXPIRED)
            store.commit()
        raise ConflictError("确认已过期或已失效，请重新预览并确认。")
    if connection.status != ConnectionStatus.ACTIVE:
        raise ConflictError("日历连接已撤销或过期，待写入操作不能执行。")


def _access_token(
    store: CalendarStore,
    provider: CalendarProvider,
    cipher: TokenCipher,
    connection: CalendarConnectionData,
) -> str:
    credentials = store.get_credentials(connection.id)
    if credentials is None:
        raise ConflictError("日历连接不存在。")
    access_token = cipher.decrypt(credentials.access_token_ciphertext)
    refresh_token = cipher.decrypt(credentials.refresh_token_ciphertext)
    now = utc_now()
    if (
        access_token
        and credentials.token_expires_at is not None
        and _as_utc(credentials.token_expires_at) > now + TOKEN_REFRESH_MARGIN
    ):
        return access_token
    if not refresh_token:
        store.set_connection_status(connection.id, ConnectionStatus.EXPIRED)
        store.commit()
        raise ConflictError(ERROR_MESSAGES[CalendarErrorCategory.AUTH])
    try:
        tokens: OAuthTokenSet = provider.refresh(refresh_token=refresh_token)
    except (CalendarProviderError, CalendarUnknownResult) as exc:
        store.set_connection_status(connection.id, ConnectionStatus.EXPIRED)
        store.commit()
        raise ConflictError(ERROR_MESSAGES[CalendarErrorCategory.AUTH]) from exc
    store.update_connection_tokens(
        connection.id,
        access_token_ciphertext=cipher.encrypt(tokens.access_token),
        refresh_token_ciphertext=(
            cipher.encrypt(tokens.refresh_token) if tokens.refresh_token else None
        ),
        token_expires_at=tokens.expires_at,
        scopes=tokens.scopes,
    )
    store.flush()
    return tokens.access_token


def _require_connection(
    store: CalendarStore, workspace_id: int, connection_id: int
) -> CalendarConnectionData:
    connection = store.get_connection_for_workspace(workspace_id, connection_id)
    if connection is None:
        raise NotFoundError("日历连接不存在")
    return connection


def _require_active_connection(
    store: CalendarStore, workspace_id: int, connection_id: int
) -> CalendarConnectionData:
    connection = _require_connection(store, workspace_id, connection_id)
    if connection.status != ConnectionStatus.ACTIVE:
        raise ConflictError("日历连接已撤销或过期，请重新连接。")
    return connection


def _require_draft(store: CalendarStore, workspace_id: int, draft_id: int) -> CalendarDraftData:
    draft = store.get_draft_for_workspace(workspace_id, draft_id)
    if draft is None:
        raise NotFoundError("日历草案不存在")
    return draft


def _current_content_fingerprint(draft: CalendarDraftData) -> str:
    return content_fingerprint(
        connection_id=draft.connection_id,
        provider=f"connection:{draft.connection_id}",
        calendar_id=draft.items[0].target_calendar_id if draft.items else "",
        item_fingerprints=[item.item_fingerprint for item in draft.items],
    )


def _connection_provider(connection: CalendarConnectionData) -> str:
    # 草案内容指纹只用于稳定比较，连接编号已足以区分账户与日历。
    return f"connection:{connection.id}"


def _validate_item_selection(
    draft: CalendarDraftData, item_ids: list[int]
) -> list[CalendarDraftItemData]:
    selected_ids = {item_id for item_id in item_ids}
    if not selected_ids:
        raise DomainError("请至少选择一条要写入外部日历的内容。")
    by_id = {item.id: item for item in draft.items}
    unknown = selected_ids - set(by_id)
    if unknown:
        raise DomainError("确认范围包含不属于该草案的条目。")
    return [by_id[item.id] for item in draft.items if item.id in selected_ids]


def _validate_items_are_current(
    repos: Repositories, workspace: WorkspaceData, draft: CalendarDraftData
) -> None:
    """K006：内部任务或计划变化后，旧草案与旧确认都不能继续执行。"""
    for item in draft.items:
        current = _current_payload_for_item(repos, workspace, item)
        if item.operation_type == OperationType.DELETE:
            if current is not None:
                raise ConflictError("要删除的任务或计划已恢复，请重新生成草案。")
            continue
        if current is None:
            raise ConflictError("草案中的任务或计划已被删除，请重新生成草案。")
        expected = source_content_fingerprint(
            source_type=item.source_type,
            source_id=item.source_id,
            payload=current,
        )
        if expected != item.content_fingerprint:
            raise ConflictError("任务或计划内容已变化，请重新预览并确认。")


def _current_payload_for_item(
    repos: Repositories, workspace: WorkspaceData, item: CalendarDraftItemData
) -> CalendarEventPayload | None:
    if item.source_type == CalendarSourceType.TASK:
        task = repos.tasks.get(item.source_id)
        if (
            task is None
            or task.workspace_id != workspace.id
            or task.progress == TaskProgress.CANCELLED
            or task.due_date is None
        ):
            return None
        return _task_payload(task, workspace.timezone)
    block = repos.plans.get_block(item.source_id)
    if block is None:
        return None
    plan = repos.plans.get_draft(block.draft_id)
    if plan is None or plan.workspace_id != workspace.id or plan.status != "accepted":
        return None
    task = repos.tasks.get(block.task_id) if block.task_id is not None else None
    if task is None or task.workspace_id != workspace.id or task.progress == TaskProgress.CANCELLED:
        return None
    return _plan_block_payload(block, workspace)


def _selected_items(draft: CalendarDraftData) -> list[CalendarDraftItemData]:
    selected = set(draft.confirmed_item_ids)
    return [item for item in draft.items if item.id in selected]


def _payload_from_item(
    item: CalendarDraftItemData, *, operation_key_value: str
) -> CalendarEventPayload:
    return CalendarEventPayload(
        title=item.title,
        timezone=item.timezone,
        date_only=item.date_only,
        starts_at=item.starts_at,
        ends_at=item.ends_at,
        location=item.location,
        operation_key=operation_key_value,
    )


def _task_payload(task: TaskData, timezone: str) -> CalendarEventPayload:
    zone = _zone(timezone)
    if task.due_time is not None:
        starts_at = datetime.combine(task.due_date, task.due_time, tzinfo=zone)
        ends_at = starts_at + timedelta(minutes=DUE_DISPLAY_MINUTES)
        date_only = False
    else:
        starts_at = datetime.combine(task.due_date, time(0, 0), tzinfo=zone)
        ends_at = starts_at + timedelta(days=1)
        date_only = True
    return CalendarEventPayload(
        title=task.title,
        timezone=timezone,
        date_only=date_only,
        starts_at=starts_at,
        ends_at=ends_at,
        location=None,
        operation_key="",
    )


def _plan_block_payload(block, workspace: WorkspaceData) -> CalendarEventPayload:
    return CalendarEventPayload(
        title=block.title,
        timezone=workspace.timezone,
        date_only=False,
        starts_at=block.starts_at,
        ends_at=block.ends_at,
        location=None,
        operation_key="",
    )


def _snapshot(title: str, payload: CalendarEventPayload) -> dict:
    return {
        "title": title,
        "starts_at": payload.starts_at.isoformat(),
        "ends_at": payload.ends_at.isoformat(),
        "timezone": payload.timezone,
        "date_only": payload.date_only,
        "location": payload.location,
    }


def _excluded(kind: str, source_id: int, title: str, code: str, message: str) -> dict:
    return {
        "source_type": kind,
        "source_id": source_id,
        "title": title,
        "code": code,
        "message": message,
    }


def _state_hash(state: str) -> str:
    return sha256(state.encode("utf-8")).hexdigest()


def _zone(timezone: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _existing_refresh(store: CalendarStore, connection_id: int) -> str | None:
    credentials = store.get_credentials(connection_id)
    return credentials.refresh_token_ciphertext if credentials is not None else None
