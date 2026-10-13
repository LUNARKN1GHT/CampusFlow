"""领域错误。API 层统一转换为 HTTP 响应。"""


class DomainError(Exception):
    """业务规则错误（400）。"""


class NotFoundError(DomainError):
    """对象不存在（404）。"""


class TransientError(DomainError):
    """临时故障（限流、超时、上游 5xx、连接中断）：处理作业可重试（J005）。"""


class PermanentError(DomainError):
    """永久失败（格式不支持、来源已删除）：重试不会成功（J005）。"""
