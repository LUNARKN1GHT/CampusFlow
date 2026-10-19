"""个人工作空间学习偏好：时区、每日/每周学习容量、休息与提交缓冲。"""

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
    weekly_capacity_minutes: int | None = None,
) -> WorkspaceData:
    """更新工作空间偏好并校验取值范围；时区必须是合法 IANA 名称。

    weekly_capacity_minutes 传 None 表示保留原值（不是清空），其余字段为最终值。
    """
    current = get_settings(repos, workspace_id)
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise DomainError("时区无效，请使用 IANA 时区名称") from exc
    weekly_capacity = (
        current.weekly_capacity_minutes
        if weekly_capacity_minutes is None
        else weekly_capacity_minutes
    )
    for value, maximum in (
        (daily_capacity_minutes, 1440),
        (weekly_capacity, 10080),
        (break_minutes, 240),
        (buffer_minutes, 1440),
    ):
        if not 0 <= value <= maximum:
            raise DomainError("学习容量、休息和缓冲必须在允许范围内")
    workspace = repos.workspaces.update_settings(
        workspace_id,
        timezone=timezone,
        daily_capacity_minutes=daily_capacity_minutes,
        break_minutes=break_minutes,
        buffer_minutes=buffer_minutes,
        weekly_capacity_minutes=weekly_capacity,
    )
    if workspace is None:
        raise NotFoundError("工作空间不存在")
    # 时区变化会移动提醒窗口；在提交前重算，避免旧时区触发残留
    from campusflow.application.reminders import sync_reminders

    sync_reminders(repos, workspace_id)
    repos.uow.commit()
    return workspace
