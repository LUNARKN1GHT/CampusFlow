"""D014 测试：图片视觉识别适配器。

验收：可定位区域与原图一致（归一化坐标）；识别失败可报告而非生成确定事实。
真实接口调用不进入自动化测试（密钥与网络依赖），以桩替换传输层验证解析逻辑。
"""

import json

import pytest

from campusflow.infrastructure.llm.errors import VisionRecognitionError
from campusflow.infrastructure.llm.vision import ZhipuVisionParser

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"fake"


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
    with pytest.raises(VisionRecognitionError, match="缺少识别内容"):
        parser.parse(PNG_BYTES, "image/png")

    parser = _make_parser({"choices": [{"message": {"content": "不是 JSON"}}]})
    with pytest.raises(VisionRecognitionError, match="JSON"):
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
    with pytest.raises(VisionRecognitionError, match="顶层"):
        parser.parse(PNG_BYTES, "image/png")


def test_items_with_non_dict_elements_is_reportable_error() -> None:
    """items 含非对象元素时报可报告错误，不泄漏 AttributeError。"""
    parser = _make_parser({"choices": [{"message": {"content": '{"items": ["文字串"]}'}}]})
    with pytest.raises(VisionRecognitionError, match="非对象"):
        parser.parse(PNG_BYTES, "image/png")


def test_items_not_a_list_is_reportable_error() -> None:
    parser = _make_parser({"choices": [{"message": {"content": '{"items": {"a": 1}}'}}]})
    with pytest.raises(VisionRecognitionError, match="items"):
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
    with pytest.raises(VisionRecognitionError, match="文本"):
        parser.parse(PNG_BYTES, "image/png")
