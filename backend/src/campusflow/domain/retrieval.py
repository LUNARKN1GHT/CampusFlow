"""Q005：交给排序器之前的资料范围与候选；不生成答案或公开引用。"""

from dataclasses import dataclass

from campusflow.domain.chunking import RetrievalBlock
from campusflow.domain.embeddings import EmbeddedText
from campusflow.domain.errors import DomainError
from campusflow.domain.indexing import IndexProgressSummary


@dataclass(frozen=True)
class RetrievalScope:
    """检索范围；None 表示该维度不加限定，指定值时只匹配完全相等的资料。

    未标清学期、课程或教学班的资料不会匹配到具体范围，也不能靠空值蒙混通过。
    """

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
    """已通过空间与范围过滤的候选块；携带归属信息与向量，供排序和建立引用。"""

    material_id: int
    title: str
    semester_id: int | None
    course_id: int | None
    class_name: str | None
    manifest_id: str
    block: RetrievalBlock
    embedding: EmbeddedText
    version_no: int = 1


@dataclass(frozen=True)
class RetrievalVersion:
    """本次范围内涉及的一个资料版本及其索引进度。

    ``unavailable_reason`` 非空表示该版本已识别为不可用（如块校验失败），
    只排除这个版本，并由覆盖提示说明原因。
    """

    material_id: int
    version_id: int
    manifest_id: str
    progress: IndexProgressSummary | None
    unavailable_reason: str | None = None
    title: str = ""


@dataclass(frozen=True)
class ScopedCandidates:
    """范围检索的完整输入；候选块与各版本进度分开，便于回答层说明覆盖情况。"""

    workspace_id: int
    scope: RetrievalScope
    profile_id: str
    candidates: tuple[RetrievalCandidate, ...]
    versions: tuple[RetrievalVersion, ...]
