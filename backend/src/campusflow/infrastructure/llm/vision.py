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
        try:
            message = raw["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise VisionRecognitionError("响应缺少识别内容") from exc
        try:
            parsed = json.loads(
                message.strip().removeprefix("```json").removeprefix("```").removesuffix("```")
            )
        except json.JSONDecodeError as exc:
            raise VisionRecognitionError("识别结果不是合法 JSON") from exc

        fragments: list[ParsedFragment] = []
        for seq, item in enumerate(parsed.get("items") or []):
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            bbox = item.get("bbox")
            if (
                isinstance(bbox, list)
                and len(bbox) == 4
                and all(isinstance(v, (int, float)) and 0 <= v <= 1 for v in bbox)
            ):
                locator = ParserLocator(kind="region", bbox=tuple(float(v) for v in bbox))
            else:
                # 无法定位的结果明确降级为整页，不伪造坐标（D014 验收）
                locator = ParserLocator(kind="page", page=1)
            confidence = item.get("confidence")
            fragments.append(
                ParsedFragment(
                    seq=seq,
                    locator=locator,
                    text=text,
                    confidence=confidence if isinstance(confidence, (int, float)) else None,
                )
            )

        failures = [
            ParseFailure(
                locator=ParserLocator(kind="page", page=1),
                reason=str(note),
            )
            for note in (parsed.get("unreadable") or [])
            if str(note).strip()
        ]
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
