"""按配置装配日历供应商。"""

from __future__ import annotations

from campusflow.application.ports.calendar import CalendarProvider
from campusflow.core.config import Settings
from campusflow.infrastructure.calendar.google import GoogleCalendarProvider
from campusflow.infrastructure.calendar.local import LocalCalendarProvider


def build_calendar_provider(settings: Settings) -> CalendarProvider:
    if settings.calendar_provider == "local":
        return LocalCalendarProvider()
    if settings.calendar_provider == "google":
        return GoogleCalendarProvider(
            client_id=settings.google_calendar_client_id,
            client_secret=settings.google_calendar_client_secret,
            redirect_uri=settings.google_calendar_redirect_uri,
        )
    raise ValueError(f"不支持的日历供应商：{settings.calendar_provider}")
