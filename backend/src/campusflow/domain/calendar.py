"""M9 外部日历领域规则：连接状态、草案确认、幂等键与错误分类。

本模块不依赖 Web 框架、ORM 或供应商 SDK。外部供应商只通过应用层端口调用。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum


class ConnectionStatus(StrEnum):
    ACTIVE = "active"
    REVOKED = "revoked"
    EXPIRED = "expired"


class DraftStatus(StrEnum):
    DRAFT = "draft"
    CONFIRMED = "confirmed"
    EXECUTING = "executing"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    UNKNOWN = "unknown"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class OperationType(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"


class OperationStatus(StrEnum):
    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"
    UNKNOWN = "unknown"
    SKIPPED = "skipped"


class CalendarErrorCategory(StrEnum):
    AUTH = "auth"
    PERMISSION = "permission"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    TRANSIENT = "transient"
    PERMANENT = "permanent"
    UNKNOWN = "unknown"


class CalendarSourceType(StrEnum):
    TASK = "task"
    PLAN_BLOCK = "plan_block"


# 确认后 15 分钟内可执行；过期必须重新确认，避免长时间保留旧授权。
CONFIRMATION_TTL = timedelta(minutes=15)
# OAuth state 10 分钟内有效，且只能使用一次。
OAUTH_STATE_TTL = timedelta(minutes=10)
# 访问令牌临近过期时提前刷新，避免请求中途失效。
TOKEN_REFRESH_MARGIN = timedelta(seconds=60)

ERROR_MESSAGES: dict[CalendarErrorCategory, str] = {
    CalendarErrorCategory.AUTH: "日历授权已失效，请重新连接后再执行。",
    CalendarErrorCategory.PERMISSION: "当前授权不允许修改该日历，请检查账户权限。",
    CalendarErrorCategory.NOT_FOUND: "目标外部事件已不存在，未修改任何其它事件。",
    CalendarErrorCategory.CONFLICT: "外部事件已被修改，确认内容与当前版本不一致。",
    CalendarErrorCategory.TRANSIENT: "外部日历暂时不可用，稍后可以只重试失败项。",
    CalendarErrorCategory.PERMANENT: "外部日历拒绝了本次操作，请检查内容后重新确认。",
    CalendarErrorCategory.UNKNOWN: "外部服务响应超时，结果尚未确认，不会显示为已同步。",
}


class CalendarProviderError(Exception):
    """供应商明确返回失败；category 决定能否重试。"""

    def __init__(self, category: CalendarErrorCategory, detail: str = "") -> None:
        self.category = category
        self.detail = detail
        super().__init__(ERROR_MESSAGES[category])


class CalendarUnknownResult(Exception):
    """写入请求可能已成功但未收到响应；必须对账后才能重试。"""

    def __init__(self, detail: str = "") -> None:
        self.detail = detail
        super().__init__(ERROR_MESSAGES[CalendarErrorCategory.UNKNOWN])


@dataclass(frozen=True)
class CalendarEventPayload:
    """写入供应商的单个事件；不包含原文、描述或联系人。"""

    title: str
    timezone: str
    date_only: bool
    starts_at: datetime
    ends_at: datetime
    location: str | None
    operation_key: str


def canonical_event_payload(payload: CalendarEventPayload) -> dict[str, object]:
    return {
        "title": payload.title,
        "timezone": payload.timezone,
        "date_only": payload.date_only,
        "starts_at": _canonical_datetime(payload.starts_at),
        "ends_at": _canonical_datetime(payload.ends_at),
        "location": payload.location,
    }


def item_fingerprint(
    *,
    operation_type: OperationType,
    source_type: CalendarSourceType,
    source_id: int,
    payload: CalendarEventPayload,
    external_event_id: str | None,
) -> str:
    canonical = {
        "operation_type": operation_type.value,
        "source_type": source_type.value,
        "source_id": source_id,
        "external_event_id": external_event_id,
        "event": canonical_event_payload(payload),
    }
    return _sha256(canonical)


def source_content_fingerprint(
    *, source_type: CalendarSourceType, source_id: int, payload: CalendarEventPayload
) -> str:
    """只描述内部来源的当前内容，用于比较已同步状态（不含操作类型）。"""
    return _sha256(
        {
            "source_type": source_type.value,
            "source_id": source_id,
            "event": canonical_event_payload(payload),
        }
    )


def content_fingerprint(
    *,
    connection_id: int,
    provider: str,
    calendar_id: str,
    item_fingerprints: list[str],
) -> str:
    canonical = {
        "connection_id": connection_id,
        "provider": provider,
        "calendar_id": calendar_id,
        "items": sorted(item_fingerprints),
    }
    return _sha256(canonical)


def confirmation_fingerprint(*, content_fingerprint: str, item_ids: list[int], version: int) -> str:
    return _sha256(
        {
            "content": content_fingerprint,
            "item_ids": sorted(item_ids),
            "version": version,
        }
    )


def operation_key(*, draft_id: int, item_id: int, item_fingerprint: str) -> str:
    return _sha256(
        {
            "draft_id": draft_id,
            "item_id": item_id,
            "item_fingerprint": item_fingerprint,
        }
    )[:48]


def confirmation_is_valid(
    *,
    status: str,
    confirmed_fingerprint: str | None,
    expires_at: datetime | None,
    expected_fingerprint: str,
    now: datetime,
) -> bool:
    if status not in {
        DraftStatus.CONFIRMED.value,
        DraftStatus.EXECUTING.value,
        DraftStatus.PARTIAL.value,
        DraftStatus.FAILED.value,
        DraftStatus.UNKNOWN.value,
    }:
        return False
    if not confirmed_fingerprint or confirmed_fingerprint != expected_fingerprint:
        return False
    if expires_at is None:
        return False
    return _as_utc(expires_at) > _as_utc(now)


def draft_status_after(*, total: int, success: int, failed: int, unknown: int) -> DraftStatus:
    if total == 0:
        return DraftStatus.FAILED
    if success == total:
        return DraftStatus.COMPLETED
    if success > 0:
        return DraftStatus.PARTIAL
    if unknown == total:
        return DraftStatus.UNKNOWN
    return DraftStatus.FAILED


def _canonical_datetime(value: datetime) -> str:
    return _as_utc(value).isoformat()


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _sha256(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def utc_now() -> datetime:
    return datetime.now(UTC)
