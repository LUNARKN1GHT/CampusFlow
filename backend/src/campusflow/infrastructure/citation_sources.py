"""Q008/Q017：读取引用时复核资料当前有效状态，不能沿用生成回答时的旧判断。"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from campusflow.domain.chunking import ChunkSource, RetrievalBlock, SourceLocation
from campusflow.domain.citations import Citation, CitationStatus, RevalidatedCitation
from campusflow.domain.errors import DomainError
from campusflow.infrastructure.db.models import Material, MaterialVersion, SourceChunk


class SqlCitationSources:
    """读取引用时按当前数据库状态复核，不信任回答生成时的旧判断。"""

    def __init__(self, session: Session):
        self.session = session

    def revalidate(
        self, workspace_id: int, citations: tuple[Citation, ...] | list[Citation]
    ) -> tuple[RevalidatedCitation, ...]:
        """逐条重算状态；返回顺序与输入一致，不改写引用快照本身。"""
        return tuple(self._one(workspace_id, citation) for citation in citations)

    def _one(self, workspace_id: int, citation: Citation) -> RevalidatedCitation:
        material = self.session.get(Material, citation.material_id)
        if material is None or material.workspace_id != workspace_id:
            # 越界编号与不存在同样按已删除处理，不暴露其他空间是否存在。
            return RevalidatedCitation(citation, CitationStatus.SOURCE_DELETED)
        if material.archived:
            return RevalidatedCitation(citation, CitationStatus.SOURCE_ARCHIVED)
        version = self.session.get(MaterialVersion, citation.version_id)
        if version is None or version.material_id != material.id:
            return RevalidatedCitation(citation, CitationStatus.SOURCE_DELETED)
        latest = self.session.scalar(
            select(func.max(MaterialVersion.version_no)).where(
                MaterialVersion.material_id == material.id
            )
        )
        # 以版本号判断是否被替代，导入时间不参与；旧版本引用不再作为当前依据。
        if version.version_no != latest:
            return RevalidatedCitation(citation, CitationStatus.VERSION_SUPERSEDED)
        chunk = self.session.get(SourceChunk, citation.source_chunk_id)
        if chunk is None or chunk.version_id != version.id:
            # 片段被删除或换到其他版本，都不能再按原位置验证。
            return RevalidatedCitation(citation, CitationStatus.SOURCE_CHANGED)
        source = ChunkSource(
            chunk.id,
            chunk.version_id,
            chunk.seq,
            chunk.text,
            SourceLocation(
                chunk.locator_type,
                chunk.locator_value,
                chunk.page,
                chunk.paragraph,
                None
                if chunk.bbox_x0 is None
                else (chunk.bbox_x0, chunk.bbox_y0, chunk.bbox_x1, chunk.bbox_y1),
                chunk.table_group,
            ),
        )
        try:
            # 位置、指纹与摘录必须仍能与当前片段逐字对上，否则视为原文被改写。
            RetrievalBlock(
                id=citation.block_id,
                source_chunk_id=citation.source_chunk_id,
                version_id=citation.version_id,
                source_fingerprint=citation.source_fingerprint,
                location=citation.location,
                start=citation.start,
                end=citation.end,
                text=citation.excerpt,
            ).validate_against(source)
        except DomainError:
            return RevalidatedCitation(citation, CitationStatus.SOURCE_CHANGED)
        return RevalidatedCitation(citation, CitationStatus.VALID)
