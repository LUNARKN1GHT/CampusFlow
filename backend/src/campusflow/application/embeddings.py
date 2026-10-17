"""Q002 显式生成入口；授权/选范围由调用方完成，索引写入留 Q004。"""

from campusflow.application.ports.embeddings import Embedder, EmbeddingProfiles
from campusflow.domain.embeddings import EmbeddedText
from campusflow.domain.errors import DomainError


def embed_registered_text(
    profiles: EmbeddingProfiles, embedder: Embedder, profile_id: str, text: str
) -> EmbeddedText:
    profile = profiles.get(profile_id)
    if profile is None or profile != embedder.profile:
        raise DomainError("向量配置未登记或与调用模型不一致")
    profile.validate_text(text)
    result = embedder.embed(text)
    result.validate_for(profile)
    return result
