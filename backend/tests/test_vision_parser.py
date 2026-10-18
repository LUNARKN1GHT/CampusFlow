"""D014 测试：图片视觉识别适配器。

验收：可定位区域与原图一致（归一化坐标）；识别失败可报告而非生成确定事实。
真实接口调用不进入自动化测试（密钥与网络依赖），以桩替换传输层验证解析逻辑。
"""

import json
import urllib.error

import pytest

from campusflow.infrastructure.llm.errors import (
    VisionInvalidOutput,
    VisionRecognitionError,
    VisionRequestRejected,
)
from campusflow.infrastructure.llm.vision import MAX_RESPONSE_BYTES, ZhipuVisionParser

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"fake"


class _Response:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self, limit: int = -1) -> bytes:
        return self._body if limit < 0 else self._body[:limit]

    def __enter__(self):
        return self

    def __exit__(self, *args) -> bool:
        return False


class _Opener:
    """按调用次数返回响应或抛异常，用来验证真实 HTTP 分支。"""

    def __init__(self, action) -> None:
        self._action = action
        self.calls = 0

    def open(self, request, timeout=None):
        self.calls += 1
        result = self._action(self.calls)
        if isinstance(result, Exception):
            raise result
        return result


def _make_parser(post_result, **kwargs) -> ZhipuVisionParser:
    parser = ZhipuVisionParser("test-key", **kwargs)

    def fake_post(payload: dict) -> dict:
        if isinstance(post_result, Exception):
            raise post_result
        return post_result

    parser._post = fake_post  # noqa: SLF001  测试桩：替换传输层
    return parser


def _api_response(items: list[dict], unreadable: list[str] | None = None) -> dict:
    content = json.dumps({"items": items, "unreadable": unreadable or []}, ensure_ascii=False)
    return {"choices": [{"message": {"content": content}}]}


def test_region_locator_matches_original_image() -> None:
    """模型返回的归一化坐标原样保留（与原图一致），不缩放不修改。"""
    parser = _make_parser(
        _api_response([{"text": "作业三", "bbox": [0.1, 0.25, 0.9, 0.5], "confidence": 0.87}])
    )
    outcome = parser.parse(PNG_BYTES, "image/png")
    fragment = outcome.fragments[0]
    assert fragment.locator.kind == "region"
    assert fragment.locator.bbox == (0.1, 0.25, 0.9, 0.5)
    assert fragment.confidence == 0.87


def test_unlocatable_result_degrades_to_full_page() -> None:
    """无法定位的结果降级为整页定位，不伪造坐标（D014 验收）。"""
    parser = _make_parser(
        _api_response(
            [
                {"text": "看得见但说不清在哪", "bbox": None, "confidence": None},
                {"text": "坐标越界", "bbox": [0.1, 0.2, 1.5, 0.4]},
            ]
        )
    )
    outcome = parser.parse(PNG_BYTES, "image/png")
    assert all(f.locator.kind == "page" and f.locator.page == 1 for f in outcome.fragments)
    assert all(f.confidence is None or isinstance(f.confidence, float) for f in outcome.fragments)


def test_unreadable_parts_reported_as_failures_not_facts() -> None:
    """模型自述的不可读区域进入失败列表，不进入片段（不生成确定事实）。"""
    parser = _make_parser(
        _api_response(
            [{"text": "可读部分", "bbox": [0.1, 0.1, 0.5, 0.2]}],
            unreadable=["下半页模糊无法辨认"],
        )
    )
    outcome = parser.parse(PNG_BYTES, "image/png")
    assert len(outcome.fragments) == 1
    assert outcome.is_partial
    assert "模糊" in outcome.failures[0].reason


def test_api_failure_raises_reportable_error() -> None:
    """接口失败时抛出可报告错误，重试耗尽后仍失败，不产生任何片段。"""
    parser = _make_parser(
        VisionRecognitionError("接口返回错误：HTTP 429"), max_retries=1, backoff_seconds=0
    )
    with pytest.raises(VisionRecognitionError, match="连续失败"):
        parser.parse(PNG_BYTES, "image/png")


def test_invalid_response_shapes_raise_error() -> None:
    parser = _make_parser({"unexpected": True})
    with pytest.raises(VisionInvalidOutput, match="缺少识别内容"):
        parser.parse(PNG_BYTES, "image/png")

    parser = _make_parser({"choices": [{"message": {"content": "不是 JSON"}}]})
    with pytest.raises(VisionInvalidOutput, match="JSON"):
        parser.parse(PNG_BYTES, "image/png")


def test_unsupported_mime_and_missing_key_rejected() -> None:
    with pytest.raises(ValueError, match="密钥"):
        ZhipuVisionParser("")
    parser = _make_parser(_api_response([]))
    with pytest.raises(ValueError, match="不支持"):
        parser.parse(PNG_BYTES, "text/plain")


def test_retry_eventually_succeeds() -> None:
    """首次失败后第二次成功：重试机制生效。"""
    parser = ZhipuVisionParser("test-key", max_retries=1, backoff_seconds=0)
    calls = {"count": 0}
    good = _api_response([{"text": "成功", "bbox": [0.0, 0.0, 0.5, 0.5]}])

    def flaky_post(payload: dict) -> dict:
        calls["count"] += 1
        if calls["count"] == 1:
            raise VisionRecognitionError("超时")
        return good

    parser._post = flaky_post  # noqa: SLF001
    outcome = parser.parse(PNG_BYTES, "image/png")
    assert calls["count"] == 2
    assert outcome.fragments[0].text == "成功"


def test_top_level_array_is_reportable_error() -> None:
    """模型返回合法 JSON 但顶层不是对象（如 []）时，报可报告错误而非泄漏 AttributeError。"""
    parser = _make_parser({"choices": [{"message": {"content": "[]"}}]})
    with pytest.raises(VisionInvalidOutput, match="顶层"):
        parser.parse(PNG_BYTES, "image/png")


def test_items_with_non_dict_elements_is_reportable_error() -> None:
    """items 含非对象元素时报可报告错误，不泄漏 AttributeError。"""
    parser = _make_parser({"choices": [{"message": {"content": '{"items": ["文字串"]}'}}]})
    with pytest.raises(VisionInvalidOutput, match="非对象"):
        parser.parse(PNG_BYTES, "image/png")


def test_items_not_a_list_is_reportable_error() -> None:
    parser = _make_parser({"choices": [{"message": {"content": '{"items": {"a": 1}}'}}]})
    with pytest.raises(VisionInvalidOutput, match="items"):
        parser.parse(PNG_BYTES, "image/png")


def test_unreadable_string_is_single_failure_not_char_split() -> None:
    """unreadable 为字符串时作为一条失败项，不逐字符拆分（评审回归）。"""
    # 构造 unreadable 为字符串的响应
    content = json.dumps(
        {"items": [{"text": "可读", "bbox": [0.1, 0.1, 0.5, 0.2]}], "unreadable": "下半页模糊"},
        ensure_ascii=False,
    )
    parser = _make_parser({"choices": [{"message": {"content": content}}]})
    outcome = parser.parse(PNG_BYTES, "image/png")
    assert len(outcome.failures) == 1
    assert outcome.failures[0].reason == "下半页模糊"


def test_bbox_order_and_confidence_range_validated() -> None:
    """bbox 顺序颠倒或越界降级为整页；confidence 越界视为未提供（评审回归）。"""
    parser = _make_parser(
        _api_response(
            [
                {"text": "顺序颠倒", "bbox": [0.9, 0.1, 0.1, 0.5], "confidence": 0.5},
                {"text": "y 颠倒", "bbox": [0.1, 0.9, 0.5, 0.1], "confidence": 1},
                {"text": "置信度越界", "bbox": [0.1, 0.1, 0.5, 0.5], "confidence": 1.7},
                {"text": "置信度为字符串", "bbox": [0.1, 0.1, 0.5, 0.5], "confidence": "high"},
            ]
        )
    )
    outcome = parser.parse(PNG_BYTES, "image/png")
    assert all(f.locator.kind == "page" for f in outcome.fragments[:2])
    assert outcome.fragments[2].locator.kind == "region"
    assert outcome.fragments[2].confidence is None
    assert outcome.fragments[3].confidence is None


def test_content_not_string_is_reportable_error() -> None:
    parser = _make_parser({"choices": [{"message": {"content": {"items": []}}}]})
    with pytest.raises(VisionInvalidOutput, match="文本"):
        parser.parse(PNG_BYTES, "image/png")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"timeout_seconds": 0},
        {"timeout_seconds": 121},
        {"max_retries": -1},
        {"max_retries": 4},
        {"max_retries": True},
        {"backoff_seconds": -1},
        {"backoff_seconds": 11},
    ],
)
def test_invalid_retry_configuration_rejected(kwargs: dict) -> None:
    """Q009：直接构造适配器也不能绕过配置边界。"""
    with pytest.raises(ValueError):
        ZhipuVisionParser("test-key", **kwargs)


def test_http_auth_error_is_permanent_and_not_retried() -> None:
    """401 等请求被拒绝属于永久失败，不做无意义重试。"""
    opener = _Opener(lambda _: urllib.error.HTTPError("https://example", 401, "no", None, None))
    parser = ZhipuVisionParser("test-key", max_retries=2, backoff_seconds=0)
    parser._opener = opener  # noqa: SLF001
    with pytest.raises(VisionRequestRejected, match="HTTP 401"):
        parser.parse(PNG_BYTES, "image/png")
    assert opener.calls == 1


def test_http_server_error_is_retried_then_reported() -> None:
    """5xx 是临时故障：按上限重试，耗尽后报告失败。"""
    opener = _Opener(lambda _: urllib.error.HTTPError("https://example", 503, "busy", None, None))
    parser = ZhipuVisionParser("test-key", max_retries=1, backoff_seconds=0)
    parser._opener = opener  # noqa: SLF001
    with pytest.raises(VisionRecognitionError, match="连续失败"):
        parser.parse(PNG_BYTES, "image/png")
    assert opener.calls == 2


def test_http_rate_limit_is_retried_then_succeeds() -> None:
    """429 是临时故障，重试成功后返回正常结果。"""
    body = json.dumps(_api_response([{"text": "重试成功", "bbox": [0.1, 0.1, 0.5, 0.5]}])).encode()
    opener = _Opener(
        lambda call: (
            urllib.error.HTTPError("https://example", 429, "slow down", None, None)
            if call == 1
            else _Response(body)
        )
    )
    parser = ZhipuVisionParser("test-key", max_retries=1, backoff_seconds=0)
    parser._opener = opener  # noqa: SLF001
    outcome = parser.parse(PNG_BYTES, "image/png")
    assert opener.calls == 2
    assert outcome.fragments[0].text == "重试成功"


def test_oversized_response_is_permanent_output_error() -> None:
    """超过读取上限的响应不可用，也不重试。"""
    body = b"{" + b" " * (MAX_RESPONSE_BYTES + 10) + b"}"
    opener = _Opener(lambda _: _Response(body))
    parser = ZhipuVisionParser("test-key", max_retries=2, backoff_seconds=0)
    parser._opener = opener  # noqa: SLF001
    with pytest.raises(VisionInvalidOutput, match="上限"):
        parser.parse(PNG_BYTES, "image/png")
    assert opener.calls == 1


def test_network_error_is_transient() -> None:
    """连接失败属于临时故障，可重试。"""
    opener = _Opener(lambda _: urllib.error.URLError("connection reset"))
    parser = ZhipuVisionParser("test-key", max_retries=0, backoff_seconds=0)
    parser._opener = opener  # noqa: SLF001
    with pytest.raises(VisionRecognitionError, match="连续失败"):
        parser.parse(PNG_BYTES, "image/png")
