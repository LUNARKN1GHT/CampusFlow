"""智谱视觉识别适配器（D014，D011 协议实现）。

调用 glm-4v-flash 识别图片文字与区域；输出遵循 D011 协议：
- 可定位区域输出 region 定位（0-1 归一化坐标，与原图一致）
- 无法定位的结果降级为整页定位（page=1），不伪造坐标
- 调用失败抛出 VisionRecognitionError 由作业层报告，不生成确定事实
"""

from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.request

from campusflow.application.ports.parsers import (
    ParsedFragment,
    ParseFailure,
    ParseOutcome,
    ParserLocator,
)
from campusflow.infrastructure.llm.errors import VisionRecognitionError

API_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
DEFAULT_MODEL = "glm-4v-flash"
SUPPORTED_MIME_TYPES = frozenset({"image/png", "image/jpeg"})


def _parse_locator(bbox: object) -> ParserLocator:
    """校验 bbox 坐标：归一化范围 0-1 且 x0<=x1、y0<=y1。

    不合法时降级为整页定位，不伪造坐标（D014 验收）。
    """
    if (
        isinstance(bbox, list)
        and len(bbox) == 4
        and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in bbox)
    ):
        x0, y0, x1, y1 = (float(v) for v in bbox)
        if 0 <= x0 <= x1 <= 1 and 0 <= y0 <= y1 <= 1:
            return ParserLocator(kind="region", bbox=(x0, y0, x1, y1))
    return ParserLocator(kind="page", page=1)


def _parse_confidence(value: object) -> float | None:
    """置信度必须是 0-1 的数字，否则视为未提供。"""
    if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 1:
        return float(value)
    return None


def _parse_unreadable(raw: object) -> list[ParseFailure]:
    """unreadable 可以是字符串（单条）或字符串列表；其他类型视为结构异常。"""
    if raw is None:
        return []
    if isinstance(raw, str):
        items = [raw]
    elif isinstance(raw, list) and all(isinstance(note, str) for note in raw):
        items = raw
    else:
        raise VisionRecognitionError("unreadable 不是字符串或字符串列表")
    return [
        ParseFailure(locator=ParserLocator(kind="page", page=1), reason=note)
        for note in items
        if note.strip()
    ]


PROMPT = (
    "请识别这张图片中的文字内容，按自然阅读区块输出 JSON：\n"
    '{"items": [{"text": "区块文字", "bbox": [x0, y0, x1, y1], "confidence": 0.0}],'
    ' "unreadable": ["无法辨认区域的说明"]}\n'
    "bbox 为 0 到 1 的归一化坐标（相对图片左上角）。看不清的区块不要编造文字；"
    "无法给出可靠坐标的区块，bbox 填 null。只返回 JSON。"
)


class ZhipuVisionParser:
    def __init__(
        self,
        api_key: str,
        *,
        model: str = DEFAULT_MODEL,
        timeout_seconds: float = 60.0,
        max_retries: int = 2,
        backoff_seconds: float = 1.0,
    ) -> None:
        if not api_key:
            raise ValueError("缺少智谱 API 密钥")
        self._api_key = api_key
        self._model = model
        self._timeout = timeout_seconds
        self._max_retries = max_retries
        self._backoff = backoff_seconds

    def parse(self, content: bytes, mime_type: str) -> ParseOutcome:
        if mime_type not in SUPPORTED_MIME_TYPES:
            raise ValueError(f"视觉识别不支持的类型：{mime_type}")
        payload = self._build_payload(content, mime_type)
        raw = self._post_with_retry(payload)
        return self._to_outcome(raw)

    def _build_payload(self, content: bytes, mime_type: str) -> dict:
        image_b64 = base64.b64encode(content).decode()
        return {
            "model": self._model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": PROMPT},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime_type};base64,{image_b64}"},
                        },
                    ],
                }
            ],
        }

    def _post_with_retry(self, payload: dict) -> dict:
        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            if attempt > 0:
                time.sleep(self._backoff * attempt)
            try:
                return self._post(payload)
            except VisionRecognitionError as exc:
                last_error = exc
        raise VisionRecognitionError(
            f"视觉识别连续失败 {self._max_retries + 1} 次：{last_error}"
        ) from last_error

    def _post(self, payload: dict) -> dict:
        request = urllib.request.Request(
            API_URL,
            data=json.dumps(payload).encode(),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                return json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            raise VisionRecognitionError(f"接口返回错误：HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise VisionRecognitionError(f"调用失败：{exc}") from exc

    def _to_outcome(self, raw: dict) -> ParseOutcome:
        """把模型响应转换为协议输出。

        所有结构异常（顶层非对象、items/unreadable 类型错误等）统一转为
        VisionRecognitionError 可报告错误，不产出部分确定片段（D014 验收）。
        """
        try:
            message = raw["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise VisionRecognitionError("响应缺少识别内容") from exc
        if not isinstance(message, str):
            raise VisionRecognitionError("识别内容不是文本")
        try:
            parsed = json.loads(
                message.strip().removeprefix("```json").removeprefix("```").removesuffix("```")
            )
        except json.JSONDecodeError as exc:
            raise VisionRecognitionError("识别结果不是合法 JSON") from exc
        if not isinstance(parsed, dict):
            raise VisionRecognitionError("识别结果顶层不是对象")

        raw_items = parsed.get("items")
        if raw_items is None:
            raw_items = []
        if not isinstance(raw_items, list):
            raise VisionRecognitionError("items 不是列表")
        if any(not isinstance(item, dict) for item in raw_items):
            raise VisionRecognitionError("items 含非对象元素")

        fragments: list[ParsedFragment] = []
        for seq, item in enumerate(raw_items):
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            fragments.append(
                ParsedFragment(
                    seq=seq,
                    locator=_parse_locator(item.get("bbox")),
                    text=text,
                    confidence=_parse_confidence(item.get("confidence")),
                )
            )

        failures = _parse_unreadable(parsed.get("unreadable"))
        return ParseOutcome(fragments=fragments, failures=failures)


class FakeVisionParser:
    """测试与开发期假适配器：不调用真实接口，返回固定结果。"""

    def __init__(self, outcome: ParseOutcome | None = None) -> None:
        self._outcome = outcome or ParseOutcome(
            fragments=[
                ParsedFragment(
                    seq=0,
                    locator=ParserLocator(kind="region", bbox=(0.1, 0.1, 0.9, 0.3)),
                    text="假识别结果",
                    confidence=0.9,
                )
            ],
            failures=[],
        )

    def parse(self, content: bytes, mime_type: str) -> ParseOutcome:
        return self._outcome
