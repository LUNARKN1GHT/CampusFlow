"""Q007：稳定引用 ID、候选集校验与来源有效性。

引用 ID 由资料、版本和块共同确定，不能仅凭模型生成的字符串成立。
“ID 合法”只表示引用属于本次候选集；语义是否支持由独立评估（Q010/Q018）判断，
不能把候选集校验当作答案正确性的证明。
"""

import hashlib
from dataclasses import dataclass
from enum import StrEnum

from campusflow.domain.chunking import SourceLocation
from campusflow.domain.errors import DomainError
from campusflow.domain.retrieval import RetrievalCandidate

MAX_CITATIONS = 20
CITATION_PREFIX = "cit_"


class CitationStatus(StrEnum):
    VALID = "valid"
    SOURCE_DELETED = "source_deleted"
    SOURCE_ARCHIVED = "source_archived"
    VERSION_SUPERSEDED = "version_superseded"
    SOURCE_CHANGED = "source_changed"


INVALID_CITATION_MESSAGES = {
    CitationStatus.SOURCE_DELETED: "引用来源已删除，不能再作为可验证依据。",
    CitationStatus.SOURCE_ARCHIVED: "引用来源已归档，当前不参与检索。",
    CitationStatus.VERSION_SUPERSEDED: "引用来源已有新版本，旧版本不再作为当前依据。",
    CitationStatus.SOURCE_CHANGED: "引用片段与已保存原文不一致，不能继续验证。",
}


def stable_citation_id(material_id: int, version_id: int, block_id: str) -> str:
    if any(type(value) is not int or value <= 0 for value in (material_id, version_id)):
        raise DomainError("引用必须绑定有效的资料与版本编号")
    if not isinstance(block_id, str) or not block_id:
        raise DomainError("引用必须绑定检索块编号")
    digest = hashlib.sha256(f"{material_id}:{version_id}:{block_id}".encode()).hexdigest()
    return f"{CITATION_PREFIX}{digest[:32]}"


@dataclass(frozen=True)
class Citation:
    id: str
    material_id: int
    version_id: int
    manifest_id: str
    block_id: str
    source_chunk_id: int
    material_title: str
    semester_id: int | None
    course_id: int | None
    class_name: str | None
    location: SourceLocation
    start: int
    end: int
    excerpt: str
    source_fingerprint: str
    version_no: int = 1

    def __post_init__(self):
        if self.id != stable_citation_id(self.material_id, self.version_id, self.block_id):
            raise DomainError("引用 ID 与资料版本或检索块不一致")
        if not isinstance(self.excerpt, str) or not self.excerpt.strip():
            raise DomainError("引用必须包含非空原文摘录")
        if type(self.start) is not int or type(self.end) is not int or self.start < 0:
            raise DomainError("引用片段位置无效")
        if type(self.source_chunk_id) is not int or self.source_chunk_id <= 0:
            raise DomainError("引用必须绑定来源片段编号")
        if self.end - self.start != len(self.excerpt):
            raise DomainError("引用摘录长度与位置范围不一致")

    @classmethod
    def from_candidate(cls, candidate: RetrievalCandidate) -> "Citation":
        block = candidate.block
        return cls(
            id=stable_citation_id(candidate.material_id, block.version_id, block.id),
            material_id=candidate.material_id,
            version_id=block.version_id,
            manifest_id=candidate.manifest_id,
            block_id=block.id,
            source_chunk_id=block.source_chunk_id,
            material_title=candidate.title,
            semester_id=candidate.semester_id,
            course_id=candidate.course_id,
            class_name=candidate.class_name,
            location=block.location,
            start=block.start,
            end=block.end,
            excerpt=block.text,
            source_fingerprint=block.source_fingerprint,
            version_no=candidate.version_no,
        )


@dataclass(frozen=True)
class RevalidatedCitation:
    citation: Citation
    status: CitationStatus

    @property
    def valid(self) -> bool:
        return self.status is CitationStatus.VALID

    @property
    def message(self) -> str | None:
        return None if self.valid else INVALID_CITATION_MESSAGES[self.status]


def build_citation_set(
    candidates: tuple[RetrievalCandidate, ...] | list[RetrievalCandidate],
) -> tuple[Citation, ...]:
    """为本次候选集建立引用；候选之外的编号永远不会出现在这里。"""
    seen: dict[str, Citation] = {}
    for candidate in candidates:
        citation = Citation.from_candidate(candidate)
        existing = seen.get(citation.id)
        if existing is not None and existing != citation:
            raise DomainError("同一引用 ID 对应了不同的候选内容")
        seen[citation.id] = citation
    return tuple(sorted(seen.values(), key=lambda item: (item.material_id, item.block_id)))


def validate_citation_ids(
    citation_ids: tuple[str, ...] | list[str],
    candidates: tuple[RetrievalCandidate, ...] | list[RetrievalCandidate],
) -> tuple[Citation, ...]:
    """模型返回的引用必须全部存在于本次候选集内；未知或重复编号直接拒绝。"""
    if isinstance(citation_ids, (str, bytes)):
        raise DomainError("引用编号必须是列表")
    ids = list(citation_ids)
    if len(ids) > MAX_CITATIONS:
        raise DomainError(f"一次回答最多引用 {MAX_CITATIONS} 条原文")
    if len(set(ids)) != len(ids):
        raise DomainError("引用编号重复，无法确定唯一依据")
    available = {citation.id: citation for citation in build_citation_set(candidates)}
    unknown = [citation_id for citation_id in ids if citation_id not in available]
    if unknown:
        raise DomainError("引用编号不属于本次检索候选，已拒绝该回答")
    return tuple(available[citation_id] for citation_id in ids)


def citation_to_dict(citation: Citation) -> dict:
    return {
        "id": citation.id,
        "material_id": citation.material_id,
        "version_id": citation.version_id,
        "manifest_id": citation.manifest_id,
        "block_id": citation.block_id,
        "source_chunk_id": citation.source_chunk_id,
        "material_title": citation.material_title,
        "semester_id": citation.semester_id,
        "course_id": citation.course_id,
        "class_name": citation.class_name,
        "location": {
            "kind": citation.location.kind,
            "value": citation.location.value,
            "page": citation.location.page,
            "paragraph": citation.location.paragraph,
            "bbox": list(citation.location.bbox) if citation.location.bbox else None,
            "table_group": citation.location.table_group,
        },
        "start": citation.start,
        "end": citation.end,
        "excerpt": citation.excerpt,
        "source_fingerprint": citation.source_fingerprint,
        "version_no": citation.version_no,
    }


def citation_from_dict(data: object) -> Citation:
    if not isinstance(data, dict) or not isinstance(data.get("location"), dict):
        raise DomainError("保存的引用结构无效")
    location = data["location"]
    bbox = location.get("bbox")
    return Citation(
        id=data["id"],
        material_id=data["material_id"],
        version_id=data["version_id"],
        manifest_id=data["manifest_id"],
        block_id=data["block_id"],
        source_chunk_id=data["source_chunk_id"],
        material_title=data["material_title"],
        semester_id=data.get("semester_id"),
        course_id=data.get("course_id"),
        class_name=data.get("class_name"),
        location=SourceLocation(
            location["kind"],
            location["value"],
            location.get("page"),
            location.get("paragraph"),
            tuple(bbox) if bbox else None,
            location.get("table_group"),
        ),
        start=data["start"],
        end=data["end"],
        excerpt=data["excerpt"],
        source_fingerprint=data["source_fingerprint"],
        version_no=data.get("version_no", 1),
    )
