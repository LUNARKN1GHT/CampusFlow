"""Q014/Q017：有范围的问答会话。

每轮保存当时的范围与引用快照；读取时重新核对来源状态。切换范围只影响后续追问，
旧回答仍标记原范围，不能把其他学期或空间的引用混入当前追问。
"""

from dataclasses import dataclass, field
from datetime import datetime

from campusflow.domain.citations import Citation, citation_from_dict, citation_to_dict
from campusflow.domain.errors import DomainError
from campusflow.domain.qa import (
    ConflictNotice,
    CoverageNotice,
    MaterialComparison,
    QaAnswer,
    QaIntentKind,
)
from campusflow.domain.retrieval import RetrievalScope


@dataclass(frozen=True)
class QaConversationData:
    """会话本身只保存当前范围与模式；每一轮的实际范围以轮次快照为准。"""

    id: int
    workspace_id: int
    scope: RetrievalScope
    mode: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class QaTurnData:
    """一轮问答的完整快照：问题、协议正文、范围、引用与各类提示，读取时不再重新检索。"""

    id: int
    conversation_id: int
    question: str
    answer: str
    insufficient: bool
    intent: QaIntentKind
    scope: RetrievalScope
    citations: tuple[Citation, ...]
    coverage: tuple[CoverageNotice, ...]
    comparisons: tuple[MaterialComparison, ...]
    conflicts: tuple[ConflictNotice, ...]
    uncertainties: tuple[str, ...]
    created_at: datetime


# 快照序列化：写入时固化范围与引用，读取时按保存内容重建并重新校验，不重新检索。
def scope_to_dict(scope: RetrievalScope) -> dict:
    return {
        "semester_id": scope.semester_id,
        "course_id": scope.course_id,
        "class_name": scope.class_name,
    }


def scope_from_dict(data: object) -> RetrievalScope:
    """反序列化范围；字段非法时统一成“会话范围无效”，不把原始异常抛给上层。"""
    if not isinstance(data, dict):
        raise DomainError("保存的会话范围无效")
    try:
        return RetrievalScope(
            data.get("semester_id"), data.get("course_id"), data.get("class_name")
        )
    except DomainError:
        raise DomainError("保存的会话范围无效") from None


def coverage_to_dict(notice: CoverageNotice) -> dict:
    return {
        "kind": notice.kind,
        "material_id": notice.material_id,
        "title": notice.title,
        "message": notice.message,
        "uncovered_source_count": notice.uncovered_source_count,
        "failed_blocks": notice.failed_blocks,
    }


def coverage_from_dict(data: object) -> CoverageNotice:
    if not isinstance(data, dict):
        raise DomainError("保存的覆盖提示无效")
    return CoverageNotice(
        kind=data["kind"],
        material_id=data["material_id"],
        title=data["title"],
        message=data["message"],
        uncovered_source_count=data.get("uncovered_source_count", 0),
        failed_blocks=data.get("failed_blocks", 0),
    )


def comparison_to_dict(item: MaterialComparison) -> dict:
    return {
        "material_id": item.material_id,
        "title": item.title,
        "semester_id": item.semester_id,
        "course_id": item.course_id,
        "class_name": item.class_name,
        "citation_ids": list(item.citation_ids),
        "excerpts": list(item.excerpts),
    }


def comparison_from_dict(data: object) -> MaterialComparison:
    if not isinstance(data, dict):
        raise DomainError("保存的对照信息无效")
    return MaterialComparison(
        material_id=data["material_id"],
        title=data["title"],
        semester_id=data.get("semester_id"),
        course_id=data.get("course_id"),
        class_name=data.get("class_name"),
        citation_ids=tuple(data["citation_ids"]),
        excerpts=tuple(data["excerpts"]),
    )


def conflict_to_dict(item: ConflictNotice) -> dict:
    return {
        "code": item.code,
        "message": item.message,
        "citation_ids": list(item.citation_ids),
        "requires_review": item.requires_review,
    }


def conflict_from_dict(data: object) -> ConflictNotice:
    if not isinstance(data, dict):
        raise DomainError("保存的冲突提示无效")
    return ConflictNotice(
        code=data["code"],
        message=data["message"],
        citation_ids=tuple(data["citation_ids"]),
        requires_review=bool(data.get("requires_review", True)),
    )


@dataclass(frozen=True)
class NewQaTurn:
    """待保存的一轮问答：问题、已校验的回答对象与本轮范围，一次性转成列值。"""

    question: str
    answer: QaAnswer
    intent: QaIntentKind = QaIntentKind.QUERY
    scope: RetrievalScope = field(default_factory=RetrievalScope)

    def to_columns(self) -> dict:
        return {
            "question": self.question,
            "answer": self.answer.answer,
            "insufficient": self.answer.insufficient,
            "intent": self.intent.value,
            "scope": scope_to_dict(self.scope),
            "citations": [citation_to_dict(item) for item in self.answer.citations],
            "coverage": [coverage_to_dict(item) for item in self.answer.coverage],
            "comparisons": [comparison_to_dict(item) for item in self.answer.comparisons],
            "conflicts": [conflict_to_dict(item) for item in self.answer.conflicts],
            "uncertainties": list(self.answer.uncertainties),
        }


def turn_from_columns(row) -> QaTurnData:
    """从保存列重建一问一答；结构损坏视为领域错误，不返回残缺轮次。"""
    try:
        intent = QaIntentKind(row.intent)
        return QaTurnData(
            id=row.id,
            conversation_id=row.conversation_id,
            question=row.question,
            answer=row.answer,
            insufficient=row.insufficient,
            intent=intent,
            scope=scope_from_dict(row.scope),
            citations=tuple(citation_from_dict(item) for item in row.citations),
            coverage=tuple(coverage_from_dict(item) for item in row.coverage),
            comparisons=tuple(comparison_from_dict(item) for item in row.comparisons),
            conflicts=tuple(conflict_from_dict(item) for item in row.conflicts),
            uncertainties=tuple(row.uncertainties),
            created_at=row.created_at,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise DomainError("保存的会话记录结构无效") from exc
