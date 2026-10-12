"""处理作业领域规则（J001）。"""

import re

MAX_ERROR_REASON_LENGTH = 300

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
