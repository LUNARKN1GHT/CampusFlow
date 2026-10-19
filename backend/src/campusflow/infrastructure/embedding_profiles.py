"""不可变的全局模型配置目录；不存密钥、原文或工作空间数据。"""

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from campusflow.domain.embeddings import EmbeddingProfile
from campusflow.domain.errors import DomainError
from campusflow.infrastructure.db.models import EmbeddingProfileRecord


class SqlEmbeddingProfiles:
    """不可变配置目录：登记幂等、同编号内容不一致即拒绝，检索结果按编号隔离。"""

    def __init__(self, session: Session):
        self.session = session

    def register(self, profile: EmbeddingProfile) -> str:
        """幂等登记；同编号重复登记仅在字段完全一致时成功，否则报错。"""
        # 不覆盖已有行：配置身份一旦登记就不可改写。
        self.session.execute(
            insert(EmbeddingProfileRecord)
            .values(
                id=profile.id,
                provider=profile.provider,
                model=profile.model,
                revision=profile.revision,
                dimensions=profile.dimensions,
                generation_config=profile.generation_config,
            )
            .on_conflict_do_nothing(index_elements=["id"])
        )
        # 冲突时读回已有配置逐字段比较，避免静默接受不同设置。
        if self.get(profile.id) != profile:
            raise DomainError("已登记的向量配置不一致")
        # 由应用调用方的事务提交，不在仓储中偷偷提交其他业务修改。
        return profile.id

    def get(self, profile_id: str) -> EmbeddingProfile | None:
        """重建配置并校验派生编号；损坏或身份不符抛领域错误。"""
        row = self.session.scalar(
            select(EmbeddingProfileRecord).where(EmbeddingProfileRecord.id == profile_id)
        )
        if row is None:
            return None
        try:
            profile = EmbeddingProfile(
                provider=row.provider,
                model=row.model,
                revision=row.revision,
                dimensions=row.dimensions,
                **row.generation_config,
            )
        except (TypeError, DomainError):
            raise DomainError("已保存的向量配置无效") from None
        if profile.id != row.id:
            raise DomainError("向量配置身份校验失败")
        return profile
