"""领域错误。API 层统一转换为 HTTP 响应。"""


class DomainError(Exception):
    """业务规则错误（400）。"""


class NotFoundError(DomainError):
    """对象不存在（404）。"""
