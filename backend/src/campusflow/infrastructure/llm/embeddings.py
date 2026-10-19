"""智谱 Embedding-3：固定地址、显式单次调用、严格校验及安全错误。"""

import http.client
import json
import math
import urllib.error
import urllib.request

from campusflow.domain.embeddings import EmbeddedText, EmbeddingProfile
from campusflow.domain.errors import DomainError, ModelOutputError, PermanentError, TransientError

API_URL = "https://open.bigmodel.cn/api/paas/v4/embeddings"
MAX_RESPONSE_BYTES = 256 * 1024


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """禁止 urllib 跟随跳转：带 Authorization 的请求不能转发到其它主机。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _unique_object(pairs):
    """解析 JSON 对象时拒绝重复键；重复字段视为无效响应，而不是后者覆盖前者。"""

    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


class ZhipuEmbedder:
    """智谱 Embedding-3 适配器；每次 embed 只发起一次请求，失败按领域错误分类，不自动重试。"""

    def __init__(self, api_key: str, *, profile: EmbeddingProfile, timeout_seconds: float = 60):
        """构造时校验密钥、配置身份与超时；非法配置在联网前直接拒绝。"""

        if (
            not isinstance(api_key, str)
            or not api_key
            or not api_key.isascii()
            or any(not 33 <= ord(c) <= 126 for c in api_key)
        ):
            raise ValueError("缺少有效的智谱密钥")
        if (
            profile.provider != "zhipu"
            or profile.model != "embedding-3"
            or profile.dimensions != 1024
        ):
            raise ValueError("当前适配器仅接入已选定的智谱 Embedding-3（1024 维）")
        if (
            type(timeout_seconds) not in (int, float)
            or not math.isfinite(timeout_seconds)
            or not 0 < timeout_seconds <= 120
        ):
            raise ValueError("向量请求等待上限必须为 0 至 120 秒")
        self._api_key = api_key
        self._profile = profile
        self._timeout = timeout_seconds
        self._opener = urllib.request.build_opener(_NoRedirect())

    @property
    def profile(self) -> EmbeddingProfile:
        return self._profile

    def embed(self, text: str) -> EmbeddedText:
        """向量化单段文本；输入先按配置限长，响应必须与配置编号、型号和维度一致。"""

        self.profile.validate_text(text)
        request = urllib.request.Request(
            API_URL,
            data=json.dumps(
                {
                    "model": self.profile.model,
                    "input": [text],
                    "dimensions": self.profile.dimensions,
                    "encoding_format": self.profile.encoding_format,
                }
            ).encode(),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
        )
        # 一次显式调用对应一次请求。临时失败交给调用方有限重试，禁止暗中重复计费。
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                data = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            status = exc.code
            exc.close()
            # 408/429/5xx 是服务抖动，可重试；其余 4xx 是请求被拒绝，重试不会成功。
            if status in (408, 429) or 500 <= status <= 599:
                raise TransientError(f"向量服务暂时不可用（HTTP {status}），可稍后重试") from None
            raise PermanentError(f"向量请求被拒绝（HTTP {status}），请检查账号和配置") from None
        except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException):
            raise TransientError("向量服务连接失败或等待超时，可稍后重试") from None
        if len(data) > MAX_RESPONSE_BYTES:
            raise ModelOutputError("向量响应超过读取上限")
        try:
            # 结构、型号、维度或数值任一不符都整体拒绝，不修补也不降级使用。
            raw = json.loads(data.decode("utf-8"), object_pairs_hook=_unique_object)
            rows = raw["data"]
            if not isinstance(rows, list) or len(rows) != 1:
                raise ValueError
            row = rows[0]
            if type(row["index"]) is not int or row["index"] != 0:
                raise ValueError
            if not isinstance(row["embedding"], list):
                raise ValueError
            result = EmbeddedText(self.profile.id, tuple(row["embedding"]), raw["model"])
            # 校验配置编号、供应商回显型号与维度，防止跨配置混用向量。
            result.validate_for(self.profile)
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            OverflowError,
            RecursionError,
            DomainError,
        ):
            raise ModelOutputError("向量响应无效或模型/维度不一致，结果未接受") from None
        return result
