"""处理作业领域规则（J001、J005、J006）。"""

import re

from campusflow.domain.errors import TransientError
from campusflow.domain.states import JobStatus

MAX_ERROR_REASON_LENGTH = 300

# 终态：作业不会再自行推进；只有这些状态才写入尝试历史（J005）
TERMINAL_JOB_STATUSES = frozenset({JobStatus.DONE, JobStatus.PARTIAL, JobStatus.FAILED})

# 允许重新处理的状态：作业没有完全成功，重试仍有意义（J005/J006）
RETRYABLE_JOB_STATUSES = frozenset({JobStatus.FAILED, JobStatus.PARTIAL})

DEFAULT_JOB_TIMEOUT_SECONDS = 900
# 索引作业按块推进，用最近一次心跳判断是否卡死（Q004）
DEFAULT_EMBED_JOB_TIMEOUT_SECONDS = 300
# 索引作业自动重投上限：超过之后只保留人工重试入口（Q004）
DEFAULT_EMBED_MAX_AUTO_RETRIES = 3

# 密钥/令牌/隐私内容模式：Bearer 令牌、sk- 密钥、key= 参数、身份证/手机号等长数字串
_SENSITIVE_PATTERNS = [
    re.compile(r"Bearer\s+\S+", re.IGNORECASE),
    re.compile(r"sk-[A-Za-z0-9]{8,}"),
    re.compile(r"(?:api[_-]?key|token|password|secret)=\S+", re.IGNORECASE),
    re.compile(r"\b\d{17}[\dXx]\b"),  # 身份证号
    re.compile(r"\b1[3-9]\d{9}\b"),  # 手机号
]


def sanitize_error_reason(raw: str | None) -> str | None:
    """清洗错误原因：不含密钥或原始隐私全文，超长截断（J001 验收）。

    先替换敏感片段再截断：输出长度受限时，已识别的敏感串都已替换完毕。
    """
    if raw is None:
        return None
    sanitized = raw
    for pattern in _SENSITIVE_PATTERNS:
        sanitized = pattern.sub("[已隐藏]", sanitized)
    if len(sanitized) > MAX_ERROR_REASON_LENGTH:
        sanitized = sanitized[:MAX_ERROR_REASON_LENGTH] + "…"
    return sanitized


def is_retryable(error: BaseException) -> bool:
    """区分可重试与永久失败（J005）。

    只有显式标记为临时故障的错误（上游限流、超时、5xx、连接中断）才可重试；
    格式不支持、来源已删除这类永久失败重试也不会成功，不得反复重投。
    """
    return isinstance(error, TransientError)


def timeout_reason(timeout_seconds: int) -> str:
    """超时回收说明：让"处理中"变成长时间停留的情况可见（J005）。"""
    # 不足一分钟也显示 1 分钟，避免写出"超过 0 分钟"这种无法理解的说明。
    minutes = max(1, timeout_seconds // 60)
    return f"处理超时：超过 {minutes} 分钟未完成，已标记为可重试失败"


def is_terminal(status: str) -> bool:
    """作业是否已到终态（J006）：前端据此停止轮询。"""
    return status in TERMINAL_JOB_STATUSES


def can_retry(status: str, retryable: bool | None) -> bool:
    """作业当前是否可以重新处理（J006 交付物）。

    两个条件同时成立才算可重试：状态没有完全成功（失败或部分成功），
    且最近一次失败被判定为临时故障。已完成、处理中与永久失败都不允许重投。
    """
    return status in RETRYABLE_JOB_STATUSES and retryable is True
