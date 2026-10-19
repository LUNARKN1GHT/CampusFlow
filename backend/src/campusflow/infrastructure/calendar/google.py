"""Google Calendar API v3 适配器。

只申请 ``calendar.app.created``：创建应用自建次级日历并管理其中的事件。
请求失败按类别映射；写请求超时或 5xx 抛 ``CalendarUnknownResult``，必须对账后
才能重试，避免重复建项。任何日志都不输出令牌、授权码或完整响应体。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from http.client import HTTPException
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from campusflow.application.ports.calendar import OAuthTokenSet, ProviderEventResult
from campusflow.domain.calendar import (
    CalendarErrorCategory,
    CalendarEventPayload,
    CalendarProviderError,
    CalendarUnknownResult,
)

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"
CALENDAR_API = "https://www.googleapis.com/calendar/v3"
# 只申请管理应用自建日历的最小范围，不接触用户已有的其他日历。
SCOPE = "openid email https://www.googleapis.com/auth/calendar.app.created"
REQUEST_TIMEOUT_SECONDS = 15


class GoogleCalendarProvider:
    """CalendarProvider 端口实现；所有方法只接收已解密的令牌，本类不持久化凭据。"""

    name = "google"

    def __init__(
        self,
        *,
        client_id: str | None,
        client_secret: str | None,
        redirect_uri: str,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri

    def _require_config(self) -> None:
        if not self._client_id or not self._client_secret:
            raise CalendarProviderError(
                CalendarErrorCategory.PERMANENT,
                "未配置 GOOGLE_CALENDAR_CLIENT_ID / GOOGLE_CALENDAR_CLIENT_SECRET",
            )

    def authorization_url(self, *, state: str, redirect_uri: str) -> str:
        """构造授权地址；offline 与 consent 确保授权后能取得 refresh token。"""
        self._require_config()
        params = {
            "client_id": self._client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": SCOPE,
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
            "include_granted_scopes": "true",
        }
        return f"{AUTH_URL}?{urlencode(params)}"

    def exchange_code(self, *, code: str, redirect_uri: str) -> OAuthTokenSet:
        """用授权码换取令牌；账户邮箱读取失败时留空，不阻断连接建立。"""
        self._require_config()
        payload = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "redirect_uri": redirect_uri,
        }
        data = self._request_form(TOKEN_URL, payload, write=False)
        access_token = str(data["access_token"])
        expires_in = int(data.get("expires_in", 3600))
        email = self._user_email(access_token)
        return OAuthTokenSet(
            access_token=access_token,
            refresh_token=data.get("refresh_token"),
            expires_at=datetime.now(UTC) + timedelta(seconds=expires_in),
            account_email=email,
            scopes=str(data.get("scope", SCOPE)),
        )

    def refresh(self, *, refresh_token: str) -> OAuthTokenSet:
        """刷新访问令牌；供应商未返回新 refresh token 时沿用旧值。"""
        self._require_config()
        payload = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": self._client_id,
            "client_secret": self._client_secret,
        }
        data = self._request_form(TOKEN_URL, payload, write=False)
        access_token = str(data["access_token"])
        expires_in = int(data.get("expires_in", 3600))
        return OAuthTokenSet(
            access_token=access_token,
            refresh_token=data.get("refresh_token") or refresh_token,
            expires_at=datetime.now(UTC) + timedelta(seconds=expires_in),
            account_email=None,
            scopes=str(data.get("scope", SCOPE)),
        )

    def revoke(self, *, token: str) -> None:
        """撤销刷新或访问令牌；失败以供应商错误抛出，由调用方决定是否阻断本地断开。"""
        self._request_form(REVOKE_URL, {"token": token}, write=False)

    def ensure_calendar(self, *, access_token: str, timezone: str) -> str:
        """在账户下创建 CampusFlow 次级日历并返回其标识；仅首次连接时调用。"""
        body = {"summary": "CampusFlow", "timeZone": timezone}
        data = self._request_json(
            "POST",
            f"{CALENDAR_API}/calendars",
            access_token=access_token,
            body=body,
        )
        calendar_id = data.get("id")
        if not calendar_id:
            raise CalendarProviderError(CalendarErrorCategory.PERMANENT, "未返回日历标识")
        return str(calendar_id)

    def create_event(
        self, *, access_token: str, calendar_id: str, event: CalendarEventPayload
    ) -> ProviderEventResult:
        """写入单个事件；超时、连接中断或 5xx 抛未知结果，须先对账再重试。"""
        url = (
            f"{CALENDAR_API}/calendars/{quote(calendar_id, safe='')}/events"
            "?sendUpdates=none&conferenceDataVersion=0"
        )
        data = self._request_json(
            "POST", url, access_token=access_token, body=self._event_body(event), write=True
        )
        return self._event_result(data)

    def update_event(
        self,
        *,
        access_token: str,
        calendar_id: str,
        external_event_id: str,
        etag: str | None,
        event: CalendarEventPayload,
    ) -> ProviderEventResult:
        """按 If-Match 更新事件；etag 不匹配时供应商返回冲突，不覆盖他人修改。"""
        url = (
            f"{CALENDAR_API}/calendars/{quote(calendar_id, safe='')}/events/"
            f"{quote(external_event_id, safe='')}?sendUpdates=none"
        )
        headers = {"If-Match": etag} if etag else None
        data = self._request_json(
            "PATCH",
            url,
            access_token=access_token,
            body=self._event_body(event),
            headers=headers,
            write=True,
        )
        return self._event_result(data)

    def delete_event(
        self,
        *,
        access_token: str,
        calendar_id: str,
        external_event_id: str,
        etag: str | None,
    ) -> None:
        """按 If-Match 删除事件；删除同样可能“已生效但响应丢失”，按未知结果处理。"""
        url = (
            f"{CALENDAR_API}/calendars/{quote(calendar_id, safe='')}/events/"
            f"{quote(external_event_id, safe='')}?sendUpdates=none"
        )
        headers = {"If-Match": etag} if etag else None
        self._request_json(
            "DELETE",
            url,
            access_token=access_token,
            headers=headers,
            write=True,
            expect_body=False,
        )

    def find_event(
        self, *, access_token: str, calendar_id: str, operation_key: str
    ) -> ProviderEventResult | None:
        """按私有扩展属性中的幂等键查找事件，供写入超时后对账。"""
        query = urlencode(
            {
                "privateExtendedProperty": f"campusflow_operation_key={operation_key}",
                "maxResults": "5",
                "showDeleted": "false",
            }
        )
        url = f"{CALENDAR_API}/calendars/{quote(calendar_id, safe='')}/events?{query}"
        data = self._request_json("GET", url, access_token=access_token)
        items = data.get("items") or []
        if not items:
            return None
        return self._event_result(items[0], existed=True)

    # ---- HTTP 辅助 --------------------------------------------------------
    def _user_email(self, access_token: str) -> str | None:
        # 邮箱只用于连接标识；读取失败不阻断授权，连接仍以空邮箱建立。
        try:
            data = self._request_json("GET", USERINFO_URL, access_token=access_token)
        except CalendarProviderError:
            return None
        email = data.get("email")
        return str(email) if email else None

    def _event_result(self, data: dict[str, Any], *, existed: bool = False) -> ProviderEventResult:
        external_id = data.get("id")
        if not external_id:
            raise CalendarProviderError(CalendarErrorCategory.PERMANENT, "未返回事件标识")
        return ProviderEventResult(
            external_event_id=str(external_id),
            etag=data.get("etag"),
            html_link=data.get("htmlLink"),
            existed=existed,
        )

    def _event_body(self, event: CalendarEventPayload) -> dict[str, Any]:
        if event.date_only:
            # 仅日期事件只写 date；时区不可解析时退回 UTC，不补造具体时刻。
            try:
                zone = ZoneInfo(event.timezone)
            except ZoneInfoNotFoundError:
                zone = UTC
            start_value = (
                event.starts_at.replace(tzinfo=zone)
                if event.starts_at.tzinfo is None
                else event.starts_at.astimezone(zone)
            )
            end_value = (
                event.ends_at.replace(tzinfo=zone)
                if event.ends_at.tzinfo is None
                else event.ends_at.astimezone(zone)
            )
            start = {"date": start_value.date().isoformat()}
            end = {"date": end_value.date().isoformat()}
        else:
            # 带时刻事件保留原时区与偏移，由供应商按该时区展示。
            timezone = event.timezone
            try:
                zone = ZoneInfo(timezone)
            except ZoneInfoNotFoundError:
                zone = UTC
            start = {
                "dateTime": event.starts_at.astimezone(zone).isoformat(),
                "timeZone": timezone,
            }
            end = {
                "dateTime": event.ends_at.astimezone(zone).isoformat(),
                "timeZone": timezone,
            }
        body: dict[str, Any] = {
            "summary": event.title,
            "start": start,
            "end": end,
            "transparency": "opaque",
            "extendedProperties": {"private": {"campusflow_operation_key": event.operation_key}},
        }
        if event.location:
            body["location"] = event.location
        return body

    def _request_form(self, url: str, payload: dict[str, str], *, write: bool) -> dict[str, Any]:
        body = urlencode(payload).encode("utf-8")
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        return self._send("POST", url, body=body, headers=headers, write=write)

    def _request_json(
        self,
        method: str,
        url: str,
        *,
        access_token: str,
        body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        write: bool = False,
        expect_body: bool = True,
    ) -> dict[str, Any]:
        request_headers = {"Authorization": f"Bearer {access_token}"}
        if headers:
            request_headers.update(headers)
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            request_headers["Content-Type"] = "application/json; charset=utf-8"
        return self._send(
            method,
            url,
            body=data,
            headers=request_headers,
            write=write,
            expect_body=expect_body,
        )

    def _send(
        self,
        method: str,
        url: str,
        *,
        body: bytes | None,
        headers: dict[str, str],
        write: bool,
        expect_body: bool = True,
    ) -> dict[str, Any]:
        request = Request(url, data=body, headers=headers, method=method)
        try:
            with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                raw = response.read()
        except HTTPError as error:
            self._raise_http_error(error, write=write)
            raise  # pragma: no cover - _raise_http_error 总是抛出
        except (URLError, TimeoutError, HTTPException) as error:
            if write:
                # 写请求可能已到达供应商：先记为未知，对账后才能重试。
                raise CalendarUnknownResult(str(error)) from error
            raise CalendarProviderError(CalendarErrorCategory.TRANSIENT, str(error)) from error
        if not expect_body or not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            if write:
                # 响应损坏同样无法判断是否已写入，按未知结果处理。
                raise CalendarUnknownResult("供应商返回了无法解析的响应") from error
            raise CalendarProviderError(
                CalendarErrorCategory.PERMANENT, "供应商返回了无法解析的响应"
            ) from error

    def _raise_http_error(self, error: HTTPError, *, write: bool) -> None:
        status = error.code
        detail = ""
        try:
            payload = json.loads(error.read().decode("utf-8"))
            detail = str(payload.get("error", {}).get("message", ""))[:300]
            reason = str(payload.get("error", {}).get("status", ""))
        except Exception:
            reason = ""
        if status == 401:
            category = CalendarErrorCategory.AUTH
        elif status == 403:
            # 限流类 403 可稍后重试；其余 403 视为权限不足，需用户处理授权。
            rate_limited = reason in {
                "RESOURCE_EXHAUSTED",
                "rateLimitExceeded",
                "userRateLimitExceeded",
            }
            category = (
                CalendarErrorCategory.TRANSIENT
                if rate_limited
                else CalendarErrorCategory.PERMISSION
            )
        elif status == 404:
            category = CalendarErrorCategory.NOT_FOUND
        elif status in {409, 412}:
            # etag 不匹配或资源已被改动：保留冲突，交给用户重新确认后再写。
            category = CalendarErrorCategory.CONFLICT
        elif status == 429:
            category = CalendarErrorCategory.TRANSIENT
        elif status >= 500:
            # 写请求的 5xx 可能已经生效；抛出未知结果，禁止直接重试。
            if write:
                raise CalendarUnknownResult(f"HTTP {status}")
            category = CalendarErrorCategory.TRANSIENT
        else:
            category = CalendarErrorCategory.PERMANENT
        raise CalendarProviderError(category, detail or f"HTTP {status}") from error
