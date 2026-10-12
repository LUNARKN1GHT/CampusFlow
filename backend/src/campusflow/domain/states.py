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


class MaterialSourceType(StrEnum):
    """资料来源类型（README §4.2）。Word/网页导入在 M6 扩展。"""

    PDF = "pdf"
    IMAGE = "image"
    TEXT = "text"
    WORD = "word"
    WEB = "web"


class MaterialStatus(StrEnum):
    """资料处理状态：解析进度与核对状态相互独立。"""

    PENDING = "pending"  # 待处理
    PROCESSING = "processing"  # 处理中
    DONE = "done"  # 完成
    PARTIAL = "partial"  # 部分成功
    FAILED = "failed"  # 失败


class JobStage(StrEnum):
    """后台处理作业的阶段。"""

    PARSE = "parse"  # 解析原文
    EXTRACT = "extract"  # 提取事项
    EMBED = "embed"  # 向量化


class JobStatus(StrEnum):
    """后台处理作业的状态（J001）。完成与部分成功区分。"""

    PENDING = "pending"  # 待处理
    RUNNING = "running"  # 处理中
    DONE = "done"  # 完成
    PARTIAL = "partial"  # 部分成功
    FAILED = "failed"  # 失败
