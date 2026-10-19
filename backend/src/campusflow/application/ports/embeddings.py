"""向量生成与配置登记端口，不暴露 HTTP 或数据库类型。"""

from typing import Protocol

from campusflow.domain.embeddings import EmbeddedText, EmbeddingProfile


class Embedder(Protocol):
    """向量生成适配器；结果必须携带生成它的配置身份，不能跨配置比较。"""

    @property
    def profile(self) -> EmbeddingProfile:
        """本次生成使用的不可变配置身份。"""
        ...

    def embed(self, text: str) -> EmbeddedText:
        """单次显式调用，不自动重试或静默降级；无效响应抛 ModelOutputError。"""
        ...


class EmbeddingProfiles(Protocol):
    """全局模型配置登记处；配置不可改写，重复登记同一身份保持幂等。"""

    def register(self, profile: EmbeddingProfile) -> str:
        """登记并返回配置 ID；已存在但内容不一致时抛错，不覆盖旧记录。"""
        ...

    def get(self, profile_id: str) -> EmbeddingProfile | None:
        """返回已登记配置；None 表示从未登记，不能据此假装有向量。"""
        ...
