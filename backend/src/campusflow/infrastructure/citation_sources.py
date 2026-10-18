"""Q008/Q017：读取引用时复核资料当前有效状态，不能沿用生成回答时的旧判断。"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from campusflow.domain.chunking import ChunkSource, RetrievalBlock, SourceLocation
from campusflow.domain.citations import Citation, CitationStatus, RevalidatedCitation
from campusflow.domain.errors import DomainError
from campusflow.infrastructure.db.models import Material, MaterialVersion, SourceChunk


class SqlCitationSources:
    def __init__(self, session: Session):
        self.session = session

    def revalidate(
        self, workspace_id: int, citations: tuple[Citation, ...] | list[Citation]
    ) -> tuple[RevalidatedCitation, ...]:
        return tuple(self._one(workspace_id, citation) for citation in citations)

    def _one(self, workspace_id: int, citation: Citation) -> RevalidatedCitation:
        material = self.session.get(Material, citation.material_id)
        if material is None or material.workspace_id != workspace_id:
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
        if version.version_no != latest:
            return RevalidatedCitation(citation, CitationStatus.VERSION_SUPERSEDED)
        chunk = self.session.get(SourceChunk, citation.source_chunk_id)
        if chunk is None or chunk.version_id != version.id:
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
