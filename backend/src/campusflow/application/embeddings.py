"""Q002 显式生成入口；Q004 起由索引编排在调用模型前登记配置。"""

from campusflow.application.ports.embeddings import Embedder, EmbeddingProfiles
from campusflow.domain.embeddings import EmbeddedText, EmbeddingProfile
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


def register_embedder_profile(profiles: EmbeddingProfiles, embedder: Embedder) -> EmbeddingProfile:
    """登记并核对向量配置（Q004）。

    配置身份决定索引行的归属；登记不一致时直接失败，不发任何网络请求。
    """
    profile = embedder.profile
    profiles.register(profile)
    stored = profiles.get(profile.id)
    if stored is None or stored != profile:
        raise DomainError("向量配置未登记或与调用模型不一致")
    return profile
