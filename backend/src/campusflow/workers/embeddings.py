"""Q002 装配；构造不会联网，Q004 才将其接入异步索引。"""

from campusflow.core.config import Settings
from campusflow.domain.embeddings import EmbeddingProfile
from campusflow.infrastructure.llm.embeddings import ZhipuEmbedder


def build_embedder(settings: Settings) -> ZhipuEmbedder:
    if not settings.zhipu_api_key:
        raise ValueError("缺少 ZHIPU_API_KEY，不能调用真实向量模型")
    return ZhipuEmbedder(
        settings.zhipu_api_key,
        profile=EmbeddingProfile(
            model=settings.embedding_model,
            revision=settings.embedding_revision,
            dimensions=settings.embedding_dimensions,
        ),
        timeout_seconds=settings.embedding_timeout_seconds,
    )
