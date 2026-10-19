"""不可变的全局模型配置目录；不存密钥、原文或工作空间数据。"""

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from campusflow.domain.embeddings import EmbeddingProfile
from campusflow.domain.errors import DomainError
from campusflow.infrastructure.db.models import EmbeddingProfileRecord


class SqlEmbeddingProfiles:
    def __init__(self, session: Session):
        self.session = session

    def register(self, profile: EmbeddingProfile) -> str:
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
        if self.get(profile.id) != profile:
            raise DomainError("已登记的向量配置不一致")
        # 由应用调用方的事务提交，不在仓储中偷偷提交其他业务修改。
        return profile.id

    def get(self, profile_id: str) -> EmbeddingProfile | None:
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
