"""Q005 的 PostgreSQL 候选读取；范围条件在加载原文/向量之前进入 SQL。

复用 Q004 完整清单校验（含短事务行锁），不把旧来源或清单外向量交给排序器。
这里不做全库 top-k，也不调用外部服务；Q006 消费返回的限定范围候选。
"""

from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from campusflow.domain.chunking import RetrievalBlock
from campusflow.domain.embeddings import EmbeddedText
from campusflow.domain.errors import DomainError
from campusflow.domain.retrieval import (
    RetrievalCandidate,
    RetrievalScope,
    RetrievalVersion,
    ScopedCandidates,
)
from campusflow.infrastructure.db.models import (
    IndexedBlock,
    IndexManifest,
    Material,
    MaterialVersion,
    ProcessingJob,
)
from campusflow.infrastructure.embedding_profiles import SqlEmbeddingProfiles
from campusflow.infrastructure.index_manifests import SqlIndexManifests


class SqlRetrievalCandidates:
    """范围候选读取器：过滤条件全部进 SQL，排序与回答留给后续用例。

    当前不使用近似最近邻查询：范围内的块全部取回后在应用层排序；向量列另有
    余弦 HNSW 索引（Q004），本读取器不依赖该索引。
    """

    def __init__(self, session: Session):
        self.session = session

    def collect(
        self, workspace_id: int, scope: RetrievalScope, profile_id: str
    ) -> ScopedCandidates:
        """返回范围内的当前版本候选块；不可用版本单独记录，不混入候选。"""
        profile = SqlEmbeddingProfiles(self.session).get(profile_id)
        if profile is None:
            # 从未登记过该配置就不会有对应向量；返回空候选让回答层明确说“未发现”，
            # 而不是把“尚未索引/配置未使用过”误报成业务错误。
            return ScopedCandidates(workspace_id, scope, profile_id, (), ())
        if profile.dimensions != 1024:
            # 索引与检索固定使用 1024 维配置；其他维度视为调用方配置错误。
            raise DomainError("检索必须使用已登记的 1024 维向量配置")
        newer = aliased(MaterialVersion)
        has_newer = (
            select(newer.id)
            .where(
                newer.material_id == Material.id,
                newer.version_no > MaterialVersion.version_no,
            )
            .exists()
        )
        statement = (
            select(Material, MaterialVersion.id, MaterialVersion.version_no, IndexManifest)
            .join(MaterialVersion, MaterialVersion.material_id == Material.id)
            .join(
                ProcessingJob,
                (ProcessingJob.version_id == MaterialVersion.id)
                & (ProcessingJob.material_id == Material.id)
                & (ProcessingJob.workspace_id == workspace_id)
                & (ProcessingJob.stage == "embed"),
            )
            .join(
                IndexManifest,
                (IndexManifest.id == ProcessingJob.manifest_id)
                & (IndexManifest.version_id == MaterialVersion.id)
                & (IndexManifest.workspace_id == workspace_id)
                & (IndexManifest.profile_id == profile_id),
            )
            # 只取当前版本：旧版本即使索引完整，也不再参与本次检索。
            .where(Material.workspace_id == workspace_id, Material.archived.is_(False), ~has_newer)
            .order_by(Material.id, MaterialVersion.id)
            # 与修改/删除共用资料行锁；所有资料按 ID 顺序锁定后再校验片段。
            .with_for_update(of=Material)
            .execution_options(populate_existing=True)
        )
        for column, value in (
            (Material.semester_id, scope.semester_id),
            (Material.course_id, scope.course_id),
            (Material.class_name, scope.class_name),
        ):
            if value is not None:
                statement = statement.where(column == value)

        candidates = []
        versions = []
        manifests = SqlIndexManifests(self.session)
        for material, version_id, version_no, manifest in self.session.execute(statement).all():
            try:
                # summary 默认重算快照，确认来源与解析覆盖在读取时仍未变化。
                progress = manifests.summary(workspace_id, manifest.id)
                # 只读清单内且已完成的块，清单外的旧向量不得混入候选。
                # 固定排序键让相同数据得到稳定候选顺序，便于复现排序与引用。
                rows = self.session.scalars(
                    select(IndexedBlock)
                    .where(
                        IndexedBlock.workspace_id == workspace_id,
                        IndexedBlock.version_id == version_id,
                        IndexedBlock.profile_id == profile_id,
                        IndexedBlock.block_id.in_(manifest.snapshot["block_ids"]),
                        IndexedBlock.state == "done",
                    )
                    .order_by(
                        IndexedBlock.source_chunk_id,
                        IndexedBlock.start_offset,
                        IndexedBlock.block_id,
                    )
                    .execution_options(populate_existing=True)
                )
                current = []
                for row in rows:
                    source = manifests.blocks._source(workspace_id, row.source_chunk_id)
                    # 逐块复核原文指纹、偏移与向量配置；不符时排除整个版本。
                    block = RetrievalBlock(
                        row.block_id,
                        row.source_chunk_id,
                        row.version_id,
                        row.source_fingerprint,
                        source.location,
                        row.start_offset,
                        row.end_offset,
                        row.text,
                        row.chunker_version,
                    )
                    block.validate_against(source)
                    embedding = EmbeddedText(
                        profile_id, tuple(float(value) for value in row.embedding), profile.model
                    )
                    embedding.validate_for(profile)
                    current.append(
                        RetrievalCandidate(
                            material.id,
                            material.title,
                            material.semester_id,
                            material.course_id,
                            material.class_name,
                            manifest.id,
                            block,
                            embedding,
                            version_no,
                        )
                    )
            except DomainError:
                # 可识别的数据失效只排除该版本；数据库/程序异常向上传递，不能冒充无资料。
                versions.append(
                    RetrievalVersion(
                        material.id,
                        version_id,
                        manifest.id,
                        None,
                        "index_unavailable",
                        material.title,
                    )
                )
                continue
            candidates.extend(current)
            versions.append(
                RetrievalVersion(
                    material.id, version_id, manifest.id, progress, None, material.title
                )
            )
        return ScopedCandidates(workspace_id, scope, profile_id, tuple(candidates), tuple(versions))
