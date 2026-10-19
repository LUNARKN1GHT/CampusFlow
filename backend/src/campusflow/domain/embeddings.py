"""Q002：向量与配置身份。配置变更必须产生新身份，不按维度猜测兼容性。"""

import hashlib
import json
import math
import re
from dataclasses import dataclass

from campusflow.domain.errors import DomainError


@dataclass(frozen=True)
class EmbeddingProfile:
    """模型配置身份；供应商、型号、本地代次、维度与生成设置共同决定可否互相比较。"""

    provider: str = "zhipu"
    model: str = "embedding-3"
    # 本地索引代次，不冒充供应商的内部模型版本；升级/重建时显式变更。
    revision: str = "q001-v1"
    dimensions: int = 1024
    encoding_format: str = "float"
    preprocessing: str = "identity-v1"
    max_input_bytes: int = 3000

    def __post_init__(self):
        for value in (self.provider, self.model, self.revision):
            if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", value):
                raise DomainError("向量模型标识或版本无效")
        if type(self.dimensions) is not int or not 1 <= self.dimensions <= 4096:
            raise DomainError("向量维度无效")
        if self.encoding_format != "float" or self.preprocessing != "identity-v1":
            raise DomainError("不支持的向量编码或文本处理设置")
        if type(self.max_input_bytes) is not int or not 1 <= self.max_input_bytes <= 3000:
            raise DomainError("向量输入上限必须为 1 至 3000 字节")

    @property
    def generation_config(self) -> dict:
        return {
            "encoding_format": self.encoding_format,
            "preprocessing": self.preprocessing,
            "max_input_bytes": self.max_input_bytes,
        }

    @property
    def id(self) -> str:
        """全部身份字段的稳定哈希；改维度、预处理或输入上限都会产生新的配置编号。"""
        identity = {
            "schema": 1,
            "provider": self.provider,
            "model": self.model,
            "revision": self.revision,
            "dimensions": self.dimensions,
            "generation_config": self.generation_config,
        }
        data = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(data).hexdigest()

    def validate_text(self, text: str) -> None:
        """输入为空或超过配置字节上限时拒绝；不能靠静默截断来迁就模型窗口。"""
        if not isinstance(text, str) or not text.strip():
            raise DomainError("向量输入不能为空")
        try:
            size = len(text.encode("utf-8"))
        except UnicodeError:
            raise DomainError("向量输入不是有效 UTF-8 文本") from None
        if size > self.max_input_bytes:
            raise DomainError("向量输入超过配置上限，请先分块；不能静默截断原文")


@dataclass(frozen=True)
class EmbeddedText:
    """一次向量化结果；携带配置编号与供应商回显型号，用于发现跨配置混用。"""

    profile_id: str
    values: tuple[float, ...]
    model_returned: str

    def validate_for(self, profile: EmbeddingProfile) -> None:
        # 供应商回显型号必须与配置一致；本地代次 revision 不参与这项比较。
        if self.profile_id != profile.id or self.model_returned != profile.model:
            raise DomainError("向量模型或配置版本不一致，禁止混用")
        if not isinstance(self.values, tuple) or len(self.values) != profile.dimensions:
            raise DomainError("向量维度与配置不一致")
        try:
            valid = all(
                type(value) in (int, float) and math.isfinite(value) for value in self.values
            ) and any(self.values)
        except OverflowError:
            valid = False
        if not valid:
            raise DomainError("向量必须是有限数字组成的非零向量")


def cosine_similarity(
    query: EmbeddedText, candidate: EmbeddedText, profile: EmbeddingProfile
) -> float:
    """供后续检索复用的带版本比较；不同模型/代次即使同维也拒绝。"""
    query.validate_for(profile)
    candidate.validate_for(profile)
    # 先按最大绝对值缩放，避免合法有限大数在乘方时溢出。
    qscale = max(abs(v) for v in query.values)
    cscale = max(abs(v) for v in candidate.values)
    q = tuple(v / qscale for v in query.values)
    c = tuple(v / cscale for v in candidate.values)
    result = math.fsum(a * b for a, b in zip(q, c, strict=True)) / (
        math.sqrt(math.fsum(v * v for v in q)) * math.sqrt(math.fsum(v * v for v in c))
    )
    return max(-1.0, min(1.0, result))
