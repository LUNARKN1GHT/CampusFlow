"""工作空间墙上时间与时间点转换；不依赖 Web 或存储。"""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from campusflow.domain.errors import DomainError


def in_workspace_timezone(value: datetime, timezone: ZoneInfo) -> datetime:
    if value.tzinfo is not None:
        return value.astimezone(timezone)
    candidates = {
        local.astimezone(UTC)
        for fold in (0, 1)
        if (local := value.replace(tzinfo=timezone, fold=fold))
        .astimezone(UTC)
        .astimezone(timezone)
        .replace(tzinfo=None)
        == value
    }
    if not candidates:
        raise DomainError("该本地时间因夏令时跳转不存在，请调整时间")
    if len(candidates) > 1:
        raise DomainError("该本地时间因夏令时回拨有歧义，请提供明确 UTC 偏移")
    return candidates.pop().astimezone(timezone)
