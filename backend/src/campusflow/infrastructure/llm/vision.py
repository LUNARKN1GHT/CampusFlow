"""智谱视觉识别适配器（D014，D011 协议实现）。

调用 glm-4v-flash 识别图片文字与区域；输出遵循 D011 协议：
- 可定位区域输出 region 定位（0-1 归一化坐标，与原图一致）
- 无法定位的结果降级为整页定位（page=1），不伪造坐标
- 调用失败抛出 VisionRecognitionFailure 子类由作业层报告，不生成确定事实
- 临时故障（限流、5xx、网络、超时）可重试；请求被拒绝与响应无效不可重试
"""

from __future__ import annotations

import base64
import http.client
import json
import math
import time
import urllib.error
import urllib.request

from campusflow.application.ports.parsers import (
    ParsedFragment,
    ParseFailure,
    ParseOutcome,
    ParserLocator,
)
from campusflow.infrastructure.llm.errors import (
    VisionInvalidOutput,
    VisionRecognitionError,
    VisionRequestRejected,
)

API_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
DEFAULT_MODEL = "glm-4v-flash"
SUPPORTED_MIME_TYPES = frozenset({"image/png", "image/jpeg"})
MAX_RESPONSE_BYTES = 1024 * 1024


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """禁止 urllib 跟随跳转：带 Authorization 的请求不能转发到其它主机。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


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
        raise VisionInvalidOutput("unreadable 不是字符串或字符串列表")
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
    """调用智谱视觉模型识别单张图片；只接受 PNG/JPEG，输出转为片段与失败项，不当作事实。"""

    def __init__(
        self,
        api_key: str,
        *,
        model: str = DEFAULT_MODEL,
        timeout_seconds: float = 60.0,
        max_retries: int = 2,
        backoff_seconds: float = 1.0,
    ) -> None:
        """校验密钥、超时与重试上限；重试只覆盖临时故障，共最多 max_retries+1 次调用。"""

        if not api_key:
            raise ValueError("缺少智谱 API 密钥")
        if not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 120:
            raise ValueError("视觉请求等待上限必须为 0 至 120 秒")
        if type(max_retries) is not int or not 0 <= max_retries <= 3:
            raise ValueError("视觉识别重试次数必须为 0 至 3 次")
        if not math.isfinite(backoff_seconds) or not 0 <= backoff_seconds <= 10:
            raise ValueError("视觉识别重试等待必须为 0 至 10 秒")
        self._api_key = api_key
        self._model = model
        self._timeout = timeout_seconds
        self._max_retries = max_retries
        self._backoff = backoff_seconds
        self._opener = urllib.request.build_opener(_NoRedirect())

    def parse(self, content: bytes, mime_type: str) -> ParseOutcome:
        """识别一张图片；不支持的类型在联网前拒绝，结构异常统一转为可报告错误。"""

        if mime_type not in SUPPORTED_MIME_TYPES:
            raise ValueError(f"视觉识别不支持的类型：{mime_type}")
        payload = self._build_payload(content, mime_type)
        raw = self._post_with_retry(payload)
        return self._to_outcome(raw)

    def _build_payload(self, content: bytes, mime_type: str) -> dict:
        # 图片以 data URL 内联发送，不提供外链，模型无法另行读取其它地址。
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
        """只重试临时故障；请求被拒绝与输出无效直接抛出，不做无意义重试。"""

        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            if attempt > 0:
                # 线性退避：等待时间随尝试次数增加，避免连续冲击上游。
                time.sleep(self._backoff * attempt)
            try:
                return self._post(payload)
            except VisionRecognitionError as exc:
                last_error = exc
        raise VisionRecognitionError(
            f"视觉识别连续失败 {self._max_retries + 1} 次：{last_error}"
        ) from last_error

    def _post(self, payload: dict) -> dict:
        """发起一次 HTTP 调用；408/429/5xx 转临时错误，其余 4xx 拒绝，响应读取上限 1 MiB。"""

        request = urllib.request.Request(
            API_URL,
            data=json.dumps(payload).encode(),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                data = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            status = exc.code
            exc.close()
            if status in (408, 429) or 500 <= status <= 599:
                raise VisionRecognitionError(
                    f"视觉识别服务暂时不可用（HTTP {status}），可稍后重试"
                ) from None
            raise VisionRequestRejected(
                f"视觉识别请求被拒绝（HTTP {status}），请检查模型及账号配置"
            ) from None
        except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException):
            raise VisionRecognitionError("视觉识别连接失败或等待超时，可稍后重试") from None
        if len(data) > MAX_RESPONSE_BYTES:
            raise VisionInvalidOutput("视觉识别响应超过读取上限")
        try:
            return json.loads(data.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise VisionInvalidOutput("视觉识别响应不是合法 JSON") from None

    def _to_outcome(self, raw: dict) -> ParseOutcome:
        """把模型响应转换为协议输出。

        所有结构异常（顶层非对象、items/unreadable 类型错误等）统一转为
        VisionInvalidOutput 可报告错误，不产出部分确定片段（D014 验收）。
        这类响应重试也不会变好，因此按永久失败处理（Q009）。
        """
        try:
            message = raw["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise VisionInvalidOutput("响应缺少识别内容") from exc
        if not isinstance(message, str):
            raise VisionInvalidOutput("识别内容不是文本")
        try:
            # 只剥掉模型包裹的 Markdown 代码块标记，不修复或补全 JSON 内容。
            parsed = json.loads(
                message.strip().removeprefix("```json").removeprefix("```").removesuffix("```")
            )
        except json.JSONDecodeError as exc:
            raise VisionInvalidOutput("识别结果不是合法 JSON") from exc
        if not isinstance(parsed, dict):
            raise VisionInvalidOutput("识别结果顶层不是对象")

        raw_items = parsed.get("items")
        if raw_items is None:
            raw_items = []
        if not isinstance(raw_items, list):
            raise VisionInvalidOutput("items 不是列表")
        if any(not isinstance(item, dict) for item in raw_items):
            raise VisionInvalidOutput("items 含非对象元素")

        fragments: list[ParsedFragment] = []
        for seq, item in enumerate(raw_items):
            # 空文本项直接跳过：模型可能对空白区域返回空字符串，不应作为片段。
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
