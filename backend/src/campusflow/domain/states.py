"""业务状态枚举。

核对状态（ReviewStatus）与任务执行进度（TaskProgress）是两条独立状态轴：
候选事项可以处于"待核对/已确认/已忽略/已失效"，与它对应的任务是否完成无关。
"""

from enum import StrEnum


class ReviewStatus(StrEnum):
    """候选事项的核对状态（资料提取结果）。"""

    PENDING = "pending"  # 待核对
    CONFIRMED = "confirmed"  # 已确认
    IGNORED = "ignored"  # 已忽略
    SUPERSEDED = "superseded"  # 已失效（被新版本取代）


class TaskProgress(StrEnum):
    """任务的执行进度。"""

    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    DONE = "done"
    CANCELLED = "cancelled"


class TaskPriority(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class EventRecurrence(StrEnum):
    """固定日程的重复规则，MVP 只支持不重复与每周重复。"""

    NONE = "none"
    WEEKLY = "weekly"
