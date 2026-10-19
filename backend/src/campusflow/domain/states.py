"""业务状态枚举。

核对状态（ReviewStatus）与任务执行进度（TaskProgress）是两条独立状态轴：
候选事项可以处于"待核对/已确认/已忽略/已失效"，与它对应的任务是否完成无关。
两者不可互相写入，也不可用其中一条轴推导另一条。
"""

from enum import StrEnum


class ReviewStatus(StrEnum):
    """候选事项的核对状态（资料提取结果）。

    状态转换受契约限制（见 candidates.REVIEW_TRANSITIONS）：已失效只能退回
    待核对，不能直接确认；未确认的候选不参与正式约束与排期。
    """

    PENDING = "pending"  # 待核对
    CONFIRMED = "confirmed"  # 已确认
    IGNORED = "ignored"  # 已忽略
    SUPERSEDED = "superseded"  # 已失效（被新版本取代）


class TaskProgress(StrEnum):
    """任务的执行进度。

    与核对状态独立维护；完成与取消都停止提醒与逾期计算，但保留历史记录。
    取消的任务可以重新打开，重新打开不会改写既有进度历史。
    """

    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    DONE = "done"
    CANCELLED = "cancelled"


class TaskPriority(StrEnum):
    """任务重要性；排期按 high、medium、low 的顺序优先。"""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class DueSourceType(StrEnum):
    """正式截止时间的来源类型；个人目标日期不需要来源。"""

    USER = "user"  # 用户补充；来源资料失效时不改为待核对截止
    DOCUMENT = "document"  # 原文规定
    INFERRED = "inferred"  # 系统推断


class EventRecurrence(StrEnum):
    """固定日程的重复规则，MVP 只支持不重复与每周重复。

    每周重复按工作空间时区的墙上时间展开，repeat_until 含当天；
    展开遇到 DST 缺失或歧义时刻时报错，不静默挪动。
    """

    NONE = "none"
    WEEKLY = "weekly"


class MaterialSourceType(StrEnum):
    """资料来源类型（README §4.2）；Word 与网页导入由 M6 加入。"""

    PDF = "pdf"
    IMAGE = "image"
    TEXT = "text"
    WORD = "word"
    WEB = "web"


class MaterialStatus(StrEnum):
    """资料处理状态：解析进度与核对状态相互独立。

    PARTIAL 表示只解析了部分内容，展示时必须同时列出未覆盖范围，
    不能当作完整解析结果使用。
    """

    PENDING = "pending"  # 待处理
    PROCESSING = "processing"  # 处理中
    DONE = "done"  # 完成
    PARTIAL = "partial"  # 部分成功
    FAILED = "failed"  # 失败


class JobStage(StrEnum):
    """后台处理作业的阶段；作业记录用 stage 区分解析、提取与索引。"""

    PARSE = "parse"  # 解析原文
    EXTRACT = "extract"  # 提取事项
    EMBED = "embed"  # 向量化


class JobStatus(StrEnum):
    """后台处理作业的状态（J001）。完成与部分成功区分。

    只有 FAILED 与 PARTIAL 允许重投，且还要求最近失败属于临时故障；
    是否可重试由 jobs.can_retry 统一判定。
    """

    PENDING = "pending"  # 待处理
    RUNNING = "running"  # 处理中
    DONE = "done"  # 完成
    PARTIAL = "partial"  # 部分成功
    FAILED = "failed"  # 失败
