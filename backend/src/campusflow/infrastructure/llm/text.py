"""X002：智谱文字适配器。固定服务地址、禁止跳转，正文仅交给应用层校验。"""

import http.client
import json
import math
import urllib.error
import urllib.request

from campusflow.application.ports.generation import GenerationRequest
from campusflow.domain.errors import PermanentError, TransientError

API_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
MAX_RESPONSE_BYTES = 1024 * 1024


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class ZhipuTextGenerator:
    def __init__(
        self, api_key: str, *, model: str, timeout_seconds: float = 60, max_tokens: int = 4096
    ):
        if not api_key or not api_key.strip() or any(c.isspace() for c in api_key):
            raise ValueError("缺少有效的智谱密钥")
        if not model or len(model) > 100 or any(c.isspace() for c in model):
            raise ValueError("文字模型名称无效")
        if not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 120:
            raise ValueError("文字请求等待上限必须为 0 至 120 秒")
        if type(max_tokens) is not int or not 1 <= max_tokens <= 8192:
            raise ValueError("文字生成长度上限必须为 1 至 8192")
        self._api_key = api_key
        self._model = model
        self._timeout = timeout_seconds
        self._max_tokens = max_tokens
        self._opener = urllib.request.build_opener(_NoRedirect())

    def generate(self, request: GenerationRequest) -> str:
        payload = {
            "model": self._model,
            "stream": False,
            "max_tokens": self._max_tokens,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": request.system_prompt},
                {"role": "user", "content": request.source_json},
            ],
        }
        http_request = urllib.request.Request(
            API_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
        )
        # 不在适配器内自动重复调用；后续队列按现有重试策略控制次数。
        try:
            with self._opener.open(http_request, timeout=self._timeout) as response:
                data = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            status = exc.code
            exc.close()
            if status == 429 or status == 408 or 500 <= status <= 599:
                raise TransientError(f"文字模型暂时不可用（HTTP {status}），可稍后重试") from None
            raise PermanentError(
                f"文字模型请求被拒绝（HTTP {status}），请检查模型及账号配置"
            ) from None
        except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException):
            raise TransientError("文字模型连接失败或等待超时，可稍后重试") from None
        if len(data) > MAX_RESPONSE_BYTES:
            raise PermanentError("文字模型响应超过读取上限")
        try:
            raw = json.loads(data.decode("utf-8"))
            choices = raw["choices"]
            if not isinstance(choices, list) or len(choices) != 1:
                raise ValueError
            choice = choices[0]
            message = choice["message"]
            if (
                choice.get("finish_reason") != "stop"
                or message.get("tool_calls")
                or message.get("function_call")
            ):
                raise ValueError
            content = message["content"]
            if not isinstance(content, str) or not content.strip():
                raise ValueError
        except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
            raise PermanentError("文字模型未返回完整有效正文，未接受截断结果或工具调用") from None
        return content


class FakeTextGenerator:
    """测试替身：返回预设原始文本，仍须经过与真实模型相同的应用校验。"""

    def __init__(self, response: str):
        self.response = response
        self.requests: list[GenerationRequest] = []

    def generate(self, request: GenerationRequest) -> str:
        self.requests.append(request)
        return self.response
