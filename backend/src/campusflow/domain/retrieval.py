"""Q005：交给排序器之前的资料范围与候选；不生成答案或公开引用。"""

from dataclasses import dataclass

from campusflow.domain.chunking import RetrievalBlock
from campusflow.domain.embeddings import EmbeddedText
from campusflow.domain.errors import DomainError
from campusflow.domain.indexing import IndexProgressSummary


@dataclass(frozen=True)
class RetrievalScope:
    semester_id: int | None = None
    course_id: int | None = None
    class_name: str | None = None

    def __post_init__(self):
        for key in (self.semester_id, self.course_id):
            if key is not None and (type(key) is not int or key <= 0):
                raise DomainError("检索学期和课程编号必须为正整数")
        if self.class_name is not None and (
            not isinstance(self.class_name, str)
            or not self.class_name.strip()
            or len(self.class_name) > 100
            or self.class_name != self.class_name.strip()
        ):
            raise DomainError("检索教学班不能为空、含首尾空格或超过 100 字")


@dataclass(frozen=True)
class RetrievalCandidate:
    material_id: int
    title: str
    semester_id: int | None
    course_id: int | None
    class_name: str | None
    manifest_id: str
    block: RetrievalBlock
    embedding: EmbeddedText


@dataclass(frozen=True)
class RetrievalVersion:
    material_id: int
    version_id: int
    manifest_id: str
    progress: IndexProgressSummary | None
    unavailable_reason: str | None = None


@dataclass(frozen=True)
class ScopedCandidates:
    workspace_id: int
    scope: RetrievalScope
    profile_id: str
    candidates: tuple[RetrievalCandidate, ...]
    versions: tuple[RetrievalVersion, ...]
