"""向量生成与配置登记端口，不暴露 HTTP 或数据库类型。"""

from typing import Protocol

from campusflow.domain.embeddings import EmbeddedText, EmbeddingProfile


class Embedder(Protocol):
    @property
    def profile(self) -> EmbeddingProfile: ...

    def embed(self, text: str) -> EmbeddedText: ...


class EmbeddingProfiles(Protocol):
    def register(self, profile: EmbeddingProfile) -> str: ...

    def get(self, profile_id: str) -> EmbeddingProfile | None: ...
