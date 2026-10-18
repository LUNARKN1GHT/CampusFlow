"""Q010：用人工标注证据测量检索召回、引用支持与跨范围错误。

评估先按案例范围过滤文档（模拟 Q005 的读取边界），再排序；
删除/归档、相似课程和不同学期样本如果出现在结果中，直接计为跨范围错误。
"""

import hashlib
from dataclasses import dataclass, field

from campusflow.application.ports.embeddings import Embedder
from campusflow.domain.chunking import RetrievalBlock, SourceLocation
from campusflow.domain.ranking import RankingMode, rank_candidates
from campusflow.domain.retrieval import RetrievalCandidate


@dataclass(frozen=True)
class EvalDocument:
    id: str
    text: str
    semester_id: int | None
    course_id: int | None
    class_name: str | None
    state: str = "active"

    @property
    def eligible(self) -> bool:
        return self.state == "active"


@dataclass(frozen=True)
class EvalCase:
    id: str
    question: str
    semester_id: int | None
    course_id: int | None
    class_name: str | None
    support: tuple[str, ...]
    documents: tuple[EvalDocument, ...]

    def matches(self, document: EvalDocument) -> bool:
        if not document.eligible:
            return False
        for expected, actual in (
            (self.semester_id, document.semester_id),
            (self.course_id, document.course_id),
            (self.class_name, document.class_name),
        ):
            if expected is not None and expected != actual:
                return False
        return True


@dataclass
class ModeResult:
    mode: str
    recall: float = 0.0
    citation_precision: float = 0.0
    mrr: float = 0.0
    cross_scope_errors: list[str] = field(default_factory=list)
    no_hit_cases: list[str] = field(default_factory=list)
    case_rows: list[dict] = field(default_factory=list)


def _candidate(document: EvalDocument, embedder: Embedder) -> RetrievalCandidate:
    embedding = embedder.embed(document.text)
    block = RetrievalBlock(
        id=f"block-{document.id}",
        source_chunk_id=1,
        version_id=1,
        source_fingerprint="0" * 64,
        location=SourceLocation("paragraph", "1", paragraph=1),
        start=0,
        end=len(document.text),
        text=document.text,
    )
    return RetrievalCandidate(
        material_id=int(hashlib.sha256(document.id.encode()).hexdigest()[:8], 16) or 1,
        title=document.id,
        semester_id=document.semester_id,
        course_id=document.course_id,
        class_name=document.class_name,
        manifest_id="eval",
        block=block,
        embedding=embedding,
    )


def evaluate_mode(
    cases: tuple[EvalCase, ...], mode: RankingMode, embedder: Embedder, *, k: int = 5
) -> ModeResult:
    result = ModeResult(mode=mode.value)
    recall_total = 0.0
    precision_total = 0.0
    reciprocal_total = 0.0
    for case in cases:
        eligible = [document for document in case.documents if case.matches(document)]
        candidates = [_candidate(document, embedder) for document in eligible]
        ranked = rank_candidates(
            candidates,
            case.question,
            mode=mode,
            limit=k,
            query_embedding=embedder.embed(case.question),
            profile=embedder.profile,
        )
        ranked_titles = [item.candidate.title for item in ranked]
        support_hits = [title for title in ranked_titles if title in case.support]
        recall_total += len(support_hits) / len(case.support) if case.support else 1.0
        precision_total += len(support_hits) / len(ranked_titles) if ranked_titles else 0.0
        first = next(
            (index for index, title in enumerate(ranked_titles, 1) if title in case.support),
            None,
        )
        reciprocal_total += 1 / first if first else 0.0
        if case.support and first is None:
            result.no_hit_cases.append(case.id)
        for title in ranked_titles:
            if title not in {document.id for document in eligible}:
                result.cross_scope_errors.append(f"{case.id}:{title}")
        result.case_rows.append(
            {
                "case": case.id,
                "ranked": ranked_titles,
                "support": list(case.support),
                "hit": bool(first),
            }
        )
    count = len(cases) or 1
    result.recall = round(recall_total / count, 4)
    result.citation_precision = round(precision_total / count, 4)
    result.mrr = round(reciprocal_total / count, 4)
    return result


def evaluate_all(
    cases: tuple[EvalCase, ...], embedder: Embedder, *, k: int = 5
) -> dict[str, ModeResult]:
    return {
        mode.value: evaluate_mode(cases, mode, embedder, k=k)
        for mode in (RankingMode.KEYWORD, RankingMode.VECTOR, RankingMode.HYBRID)
    }
