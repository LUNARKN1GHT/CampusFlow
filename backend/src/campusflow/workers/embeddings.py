"""向量模型装配（Q002、Q004）。

真实适配器构造不会联网；`--demo` 使用确定性假向量，只允许在测试库运行，
不产生任何模型调用与费用。
"""

import hashlib

from campusflow.application.ports.embeddings import Embedder
from campusflow.core.config import Settings
from campusflow.domain.embeddings import EmbeddedText, EmbeddingProfile
from campusflow.infrastructure.llm.embeddings import ZhipuEmbedder

_FACTORY = None


def profile_from_settings(settings: Settings) -> EmbeddingProfile:
    return EmbeddingProfile(
        model=settings.embedding_model,
        revision=settings.embedding_revision,
        dimensions=settings.embedding_dimensions,
    )


def build_embedder(settings: Settings) -> ZhipuEmbedder:
    if not settings.zhipu_api_key:
        raise ValueError("缺少 ZHIPU_API_KEY，不能调用真实向量模型")
    return ZhipuEmbedder(
        settings.zhipu_api_key,
        profile=profile_from_settings(settings),
        timeout_seconds=settings.embedding_timeout_seconds,
    )


class DemoEmbedder:
    """确定性假向量：同一段文字永远得到同一个向量，不联网、不花钱。"""

    def __init__(self, profile: EmbeddingProfile):
        self._profile = profile

    @property
    def profile(self) -> EmbeddingProfile:
        return self._profile

    def embed(self, text: str) -> EmbeddedText:
        self._profile.validate_text(text)
        seed = hashlib.sha256(f"{self._profile.id}\x00{text}".encode()).digest()
        need = self._profile.dimensions * 4
        raw = b"".join(
            hashlib.sha256(seed + index.to_bytes(2, "big")).digest()
            for index in range(need // 32 + 1)
        )
        values = tuple(
            int.from_bytes(raw[index * 4 : index * 4 + 4], "big") / 2**31 - 1.0
            for index in range(self._profile.dimensions)
        )
        result = EmbeddedText(self._profile.id, values, self._profile.model)
        result.validate_for(self._profile)
        return result


def build_demo_embedder(settings: Settings) -> DemoEmbedder:
    return DemoEmbedder(profile_from_settings(settings))


def set_embedder_factory(factory) -> None:
    """由 Worker 装配入口注入（与解析器注册表同型）；传 None 恢复真实模型。"""
    global _FACTORY
    _FACTORY = factory


def make_embedder(settings: Settings) -> Embedder:
    if _FACTORY is not None:
        return _FACTORY(settings)
    return build_embedder(settings)
