"""处理作业领域规则（J001、J005）。"""

import re

from campusflow.domain.errors import TransientError
from campusflow.domain.states import JobStatus

MAX_ERROR_REASON_LENGTH = 300

# 终态：作业不会再自行推进；只有这些状态才写入尝试历史（J005）
TERMINAL_JOB_STATUSES = frozenset({JobStatus.DONE, JobStatus.PARTIAL, JobStatus.FAILED})

DEFAULT_JOB_TIMEOUT_SECONDS = 900

# 密钥/令牌/隐私内容模式：Bearer 令牌、sk- 密钥、key= 参数、身份证/手机号等长数字串
_SENSITIVE_PATTERNS = [
    re.compile(r"Bearer\s+\S+", re.IGNORECASE),
    re.compile(r"sk-[A-Za-z0-9]{8,}"),
    re.compile(r"(?:api[_-]?key|token|password|secret)=\S+", re.IGNORECASE),
    re.compile(r"\b\d{17}[\dXx]\b"),  # 身份证号
    re.compile(r"\b1[3-9]\d{9}\b"),  # 手机号
]


def sanitize_error_reason(raw: str | None) -> str | None:
    """清洗错误原因：不含密钥或原始隐私全文，超长截断（J001 验收）。"""
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
    minutes = max(1, timeout_seconds // 60)
    return f"处理超时：超过 {minutes} 分钟未完成，已标记为可重试失败"
