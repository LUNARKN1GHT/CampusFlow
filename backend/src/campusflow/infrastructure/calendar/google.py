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
SCOPE = "openid email https://www.googleapis.com/auth/calendar.app.created"
REQUEST_TIMEOUT_SECONDS = 15


class GoogleCalendarProvider:
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
        self._request_form(REVOKE_URL, {"token": token}, write=False)

    def ensure_calendar(self, *, access_token: str, timezone: str) -> str:
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
            start = {"date": event.starts_at.date().isoformat()}
            end = {"date": event.ends_at.date().isoformat()}
        else:
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
                raise CalendarUnknownResult(str(error)) from error
            raise CalendarProviderError(CalendarErrorCategory.TRANSIENT, str(error)) from error
        if not expect_body or not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            if write:
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
            category = CalendarErrorCategory.CONFLICT
        elif status == 429:
            category = CalendarErrorCategory.TRANSIENT
        elif status >= 500:
            if write:
                raise CalendarUnknownResult(f"HTTP {status}")
            category = CalendarErrorCategory.TRANSIENT
        else:
            category = CalendarErrorCategory.PERMANENT
        raise CalendarProviderError(category, detail or f"HTTP {status}") from error
