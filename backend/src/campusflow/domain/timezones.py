"""工作空间墙上时间与时间点转换；不依赖 Web 或存储。

约定：用户输入的时间不带偏移时按工作空间时区解释；带偏移时保留原时间点。
不带偏移的时间遇到 DST 缺失或歧义必须报错，不能任选一个偏移悄悄移动输入。
"""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from campusflow.domain.errors import DomainError


def in_workspace_timezone(value: datetime, timezone: ZoneInfo) -> datetime:
    """把输入解释为工作空间时区的时间点。

    带 tzinfo 的值只做时区换算，瞬间不变；不带 tzinfo 的值视为工作空间
    墙上时间。夏令时跳过的时刻与回拨造成的重复时刻都抛 DomainError，
    因为无法确定用户指的是哪一个真实瞬间。
    """
    if value.tzinfo is not None:
        return value.astimezone(timezone)
    # 分别按 fold=0／1 解释，再转回墙上时间核对：只有能往返的时刻才真实存在。
    # 集合同时完成去重：两个 fold 若指向同一瞬间，只算一个候选。
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
