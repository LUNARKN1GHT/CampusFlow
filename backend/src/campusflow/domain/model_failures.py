"""Q009：模型失败分类与用户可见文案。

只定义契约，不触发业务逻辑：问答入口用 ``classify_model_failure`` 把模型
异常转成固定的失败类别与文案，避免把异常原文、HTTP 细节或密钥直接展示给用户。

失败时不返回任何答案或引用——类别的存在只为说明「为什么没有结果」。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from campusflow.domain.errors import (
    DomainError,
    ModelOutputError,
    PermanentError,
    TransientError,
)

SERVICE_UNAVAILABLE_MESSAGE = "模型服务暂时不可用，请稍后重试。本次未生成回答或引用。"
REQUEST_REJECTED_MESSAGE = (
    "模型服务拒绝了本次请求，请联系管理员检查账号或模型配置。本次未生成回答或引用。"
)
INVALID_OUTPUT_MESSAGE = "模型返回的内容无法使用，请检查资料后重试。本次未生成回答或引用。"
INSUFFICIENT_MATERIALS_MESSAGE = (
    "现有资料中没有找到足以回答该问题的依据，请补充资料后再试。本次未生成回答或引用。"
)


class ModelFailureKind(StrEnum):
    """模型失败的可见类别。"""

    SERVICE_UNAVAILABLE = "service_unavailable"
    REQUEST_REJECTED = "request_rejected"
    INVALID_OUTPUT = "invalid_output"


@dataclass(frozen=True)
class ModelFailure:
    """一次模型失败的可见说明；retryable 决定 API 返回 503 还是 502。"""

    kind: ModelFailureKind
    retryable: bool
    message: str


def classify_model_failure(error: Exception) -> ModelFailure | None:
    """把模型调用异常转成用户可见的失败说明。

    不是模型失败的领域错误（``NotFoundError`` 等）返回 ``None``，由调用方
    按各自的业务语义处理。未预期的异常一律按「服务不可用」处理：宁可提示
    稍后重试，也不返回可能被误认为答案的内容。
    """
    if isinstance(error, TransientError):
        return ModelFailure(
            kind=ModelFailureKind.SERVICE_UNAVAILABLE,
            retryable=True,
            message=SERVICE_UNAVAILABLE_MESSAGE,
        )
    # ModelOutputError 是 PermanentError 的子类，必须先判断，否则会被归为请求被拒。
    if isinstance(error, ModelOutputError):
        return ModelFailure(
            kind=ModelFailureKind.INVALID_OUTPUT,
            retryable=False,
            message=INVALID_OUTPUT_MESSAGE,
        )
    if isinstance(error, PermanentError):
        return ModelFailure(
            kind=ModelFailureKind.REQUEST_REJECTED,
            retryable=False,
            message=REQUEST_REJECTED_MESSAGE,
        )
    if isinstance(error, DomainError):
        return None
    return ModelFailure(
        kind=ModelFailureKind.SERVICE_UNAVAILABLE,
        retryable=True,
        message=SERVICE_UNAVAILABLE_MESSAGE,
    )
