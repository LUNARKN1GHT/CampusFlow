"""Q009：模型失败分类与用户可见文案契约。"""

import pytest

from campusflow.application.candidate_generation import InvalidModelOutput
from campusflow.domain.errors import (
    DomainError,
    ModelOutputError,
    NotFoundError,
    PermanentError,
    TransientError,
)
from campusflow.domain.model_failures import (
    INSUFFICIENT_MATERIALS_MESSAGE,
    SERVICE_UNAVAILABLE_MESSAGE,
    ModelFailureKind,
    classify_model_failure,
)
from campusflow.infrastructure.llm.errors import (
    VisionInvalidOutput,
    VisionRecognitionError,
    VisionRequestRejected,
)


@pytest.mark.parametrize(
    ("error", "kind", "retryable"),
    [
        (TransientError("上游超时"), ModelFailureKind.SERVICE_UNAVAILABLE, True),
        (ModelOutputError("结构无效"), ModelFailureKind.INVALID_OUTPUT, False),
        (InvalidModelOutput("证据无效"), ModelFailureKind.INVALID_OUTPUT, False),
        (PermanentError("HTTP 401"), ModelFailureKind.REQUEST_REJECTED, False),
        (RuntimeError("未预期"), ModelFailureKind.SERVICE_UNAVAILABLE, True),
    ],
)
def test_exception_types_map_to_failure_kind(error, kind, retryable) -> None:
    failure = classify_model_failure(error)
    assert failure is not None
    assert failure.kind is kind
    assert failure.retryable is retryable


@pytest.mark.parametrize(
    ("error", "kind", "retryable"),
    [
        (VisionRecognitionError("HTTP 503"), ModelFailureKind.SERVICE_UNAVAILABLE, True),
        (VisionRequestRejected("HTTP 401"), ModelFailureKind.REQUEST_REJECTED, False),
        (VisionInvalidOutput("bad json"), ModelFailureKind.INVALID_OUTPUT, False),
    ],
)
def test_vision_errors_keep_their_semantics(error, kind, retryable) -> None:
    failure = classify_model_failure(error)
    assert failure is not None
    assert failure.kind is kind
    assert failure.retryable is retryable


def test_non_model_domain_error_is_not_classified() -> None:
    """NotFoundError 等业务错误由调用方按各自语义处理，不冒充模型失败。"""
    assert classify_model_failure(NotFoundError("不存在")) is None
    assert classify_model_failure(DomainError("校验失败")) is None


def test_messages_are_distinct_and_promise_no_answer() -> None:
    messages = {
        classify_model_failure(TransientError()).message,
        classify_model_failure(ModelOutputError()).message,
        classify_model_failure(PermanentError()).message,
        INSUFFICIENT_MATERIALS_MESSAGE,
    }
    assert len(messages) == 4
    for message in messages:
        assert "未生成回答或引用" in message


def test_insufficient_materials_is_clearly_different() -> None:
    """验收 2 的文案契约：资料不足指向补充资料，而不是服务故障。"""
    assert "补充资料" in INSUFFICIENT_MATERIALS_MESSAGE
    assert INSUFFICIENT_MATERIALS_MESSAGE != SERVICE_UNAVAILABLE_MESSAGE
    assert "不可用" not in INSUFFICIENT_MATERIALS_MESSAGE
    assert "补充资料" not in SERVICE_UNAVAILABLE_MESSAGE


def test_classified_message_does_not_echo_exception_detail() -> None:
    """用户文案是固定文本，不携带异常原文、密钥或正文。"""
    secret = "Bearer sk-live-secret 学生隐私原文"
    failure = classify_model_failure(TransientError(secret))
    assert failure is not None
    assert secret not in failure.message
    assert "sk-live-secret" not in failure.message
    assert "隐私原文" not in failure.message
