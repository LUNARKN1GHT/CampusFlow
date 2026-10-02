"""个人工作空间学习偏好用例。"""

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from campusflow.application.ports.repositories import Repositories, WorkspaceData
from campusflow.domain.errors import DomainError, NotFoundError


def get_settings(repos: Repositories, workspace_id: int) -> WorkspaceData:
    workspace = repos.workspaces.get(workspace_id)
    if workspace is None:
        raise NotFoundError("工作空间不存在")
    return workspace


def update_settings(
    repos: Repositories,
    workspace_id: int,
    *,
    timezone: str,
    daily_capacity_minutes: int,
    break_minutes: int,
    buffer_minutes: int,
) -> WorkspaceData:
    try:
        ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise DomainError("时区无效，请使用 IANA 时区名称") from exc
    workspace = repos.workspaces.update_settings(
        workspace_id,
        timezone=timezone,
        daily_capacity_minutes=daily_capacity_minutes,
        break_minutes=break_minutes,
        buffer_minutes=buffer_minutes,
    )
    if workspace is None:
        raise NotFoundError("工作空间不存在")
    repos.uow.commit()
    return workspace
