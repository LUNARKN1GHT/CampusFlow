"""本地确定性日历供应商：仅用于自动化验收与显式本地演示。

它不访问任何外部服务，事件保存在进程内；重启后丢失。生产环境必须使用
``CAMPUSFLOW_CALENDAR_PROVIDER=google``，界面与验收记录不得把本地模拟当作
真实外部同步。
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

from campusflow.application.ports.calendar import OAuthTokenSet, ProviderEventResult
from campusflow.domain.calendar import (
    CalendarErrorCategory,
    CalendarEventPayload,
    CalendarProviderError,
    CalendarUnknownResult,
)


class LocalCalendarProvider:
    """进程内模拟供应商：无网络、无持久化，重启即清空。

    幂等键与 etag 语义对齐 Google 适配器，但结果只用于验收，不代表真实同步。
    """

    name = "local"

    def __init__(self) -> None:
        self.events: dict[str, dict[str, object]] = {}
        self.operation_index: dict[str, str] = {}
        self._counter = itertools.count(1)
        self._failures: list[str] = []

    # ---- 测试辅助：按调用顺序注入一次失败 ---------------------------------
    def fail_next(self, kind: str) -> None:
        """按调用顺序排队注入一次故障，供验收覆盖未知、冲突与权限分支。"""
        self._failures.append(kind)

    def reset(self) -> None:
        """清空模拟事件、幂等索引与待注入故障。"""
        self.events.clear()
        self.operation_index.clear()
        self._failures.clear()

    def _maybe_fail(self, *, write: bool) -> str | None:
        """消费一次注入；返回 ``unknown_after_write`` 表示写入后再抛未知。"""
        if not self._failures:
            return None
        kind = self._failures.pop(0)
        if kind == "unknown_after_write":
            return kind
        if kind == "unknown":
            raise CalendarUnknownResult("模拟响应丢失")
        if kind == "not_found":
            raise CalendarProviderError(CalendarErrorCategory.NOT_FOUND)
        if kind == "conflict":
            raise CalendarProviderError(CalendarErrorCategory.CONFLICT)
        if kind == "permission":
            raise CalendarProviderError(CalendarErrorCategory.PERMISSION)
        if kind == "auth":
            raise CalendarProviderError(CalendarErrorCategory.AUTH)
        if write:
            raise CalendarProviderError(CalendarErrorCategory.TRANSIENT)
        raise CalendarProviderError(CalendarErrorCategory.TRANSIENT)

    # ---- 端口实现 ---------------------------------------------------------
    def authorization_url(self, *, state: str, redirect_uri: str) -> str:
        """直接返回带 state 与固定授权码的回调地址，不跳转真实授权页。"""
        return f"{redirect_uri}?state={quote(state)}&code=student%40example.com"

    def exchange_code(self, *, code: str, redirect_uri: str) -> OAuthTokenSet:
        """按邮箱构造固定令牌；以 error 开头的授权码模拟权限失败。"""
        if code.startswith("error"):
            raise CalendarProviderError(CalendarErrorCategory.PERMISSION)
        email = code if "@" in code else "student@example.com"
        return OAuthTokenSet(
            access_token=f"local-access-{email}",
            refresh_token=f"local-refresh-{email}",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            account_email=email,
            scopes="calendar.app.created",
        )

    def refresh(self, *, refresh_token: str) -> OAuthTokenSet:
        """从本地刷新令牌还原邮箱，并续发一小时有效的访问令牌。"""
        email = refresh_token.removeprefix("local-refresh-")
        return OAuthTokenSet(
            access_token=f"local-access-{email}",
            refresh_token=refresh_token,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            account_email=email,
            scopes="calendar.app.created",
        )

    def revoke(self, *, token: str) -> None:
        return None

    def ensure_calendar(self, *, access_token: str, timezone: str) -> str:
        """固定返回本地日历标识，不创建真实日历。"""
        return "campusflow-local"

    def create_event(
        self, *, access_token: str, calendar_id: str, event: CalendarEventPayload
    ) -> ProviderEventResult:
        """按 operation_key 幂等：重复创建返回既有事件并标记 existed。"""
        kind = self._maybe_fail(write=True)
        existing = self.operation_index.get(event.operation_key)
        if existing is not None:
            return ProviderEventResult(
                external_event_id=existing,
                etag=self.events[existing]["etag"],
                existed=True,
            )
        external_id = f"local-event-{next(self._counter)}"
        etag = f"etag-{external_id}"
        self.events[external_id] = {
            "etag": etag,
            "operation_key": event.operation_key,
            "title": event.title,
            "starts_at": event.starts_at,
            "ends_at": event.ends_at,
            "timezone": event.timezone,
            "date_only": event.date_only,
            "location": event.location,
        }
        self.operation_index[event.operation_key] = external_id
        if kind == "unknown_after_write":
            # 事件已保存但响应丢失：重试会命中幂等索引，对账应返回同一条。
            raise CalendarUnknownResult("模拟写入成功但响应丢失")
        return ProviderEventResult(external_event_id=external_id, etag=etag)

    def update_event(
        self,
        *,
        access_token: str,
        calendar_id: str,
        external_event_id: str,
        etag: str | None,
        event: CalendarEventPayload,
    ) -> ProviderEventResult:
        """etag 不匹配时返回冲突，模拟供应商的乐观并发校验。"""
        kind = self._maybe_fail(write=True)
        row = self.events.get(external_event_id)
        if row is None:
            raise CalendarProviderError(CalendarErrorCategory.NOT_FOUND)
        if etag is not None and row["etag"] != etag:
            raise CalendarProviderError(CalendarErrorCategory.CONFLICT)
        new_etag = f"etag-{external_event_id}-{next(self._counter)}"
        row.update(
            {
                "etag": new_etag,
                "operation_key": event.operation_key,
                "title": event.title,
                "starts_at": event.starts_at,
                "ends_at": event.ends_at,
                "timezone": event.timezone,
                "date_only": event.date_only,
                "location": event.location,
            }
        )
        self.operation_index[event.operation_key] = external_event_id
        if kind == "unknown_after_write":
            # 更新已生效但响应丢失：保留新 etag，对账可确认成功。
            raise CalendarUnknownResult("模拟更新成功但响应丢失")
        return ProviderEventResult(external_event_id=external_event_id, etag=new_etag)

    def delete_event(
        self,
        *,
        access_token: str,
        calendar_id: str,
        external_event_id: str,
        etag: str | None,
    ) -> None:
        """删除不存在的事件返回未找到；etag 不匹配返回冲突。"""
        kind = self._maybe_fail(write=True)
        row = self.events.get(external_event_id)
        if row is None:
            raise CalendarProviderError(CalendarErrorCategory.NOT_FOUND)
        if etag is not None and row["etag"] != etag:
            raise CalendarProviderError(CalendarErrorCategory.CONFLICT)
        self.events.pop(external_event_id, None)
        if kind == "unknown_after_write":
            # 删除已生效但响应丢失：对账查不到事件时应记为成功。
            raise CalendarUnknownResult("模拟删除成功但响应丢失")

    def find_event(
        self, *, access_token: str, calendar_id: str, operation_key: str
    ) -> ProviderEventResult | None:
        """按 operation_key 查找已写入事件，模拟超时后的对账查询。"""
        external_id = self.operation_index.get(operation_key)
        if external_id is None or external_id not in self.events:
            return None
        return ProviderEventResult(
            external_event_id=external_id,
            etag=self.events[external_id]["etag"],
            existed=True,
        )
