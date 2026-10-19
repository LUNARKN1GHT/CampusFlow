"""Q014/Q017：会话用例。

创建和切换范围都重新做服务端授权；每轮保存当轮范围与引用快照，追问不跨
学期或空间；读取旧轮次时重新核对引用来源，不能让已删除资料继续显示为
可验证依据。
"""

from dataclasses import dataclass

from campusflow.application.ports.conversations import ConversationStore
from campusflow.application.ports.repositories import Repositories
from campusflow.application.ports.retrieval import CitationSources
from campusflow.application.retrieval import validate_retrieval_scope
from campusflow.domain.citations import Citation, RevalidatedCitation
from campusflow.domain.conversation import (
    NewQaTurn,
    QaConversationData,
    QaTurnData,
)
from campusflow.domain.errors import DomainError, NotFoundError
from campusflow.domain.qa import QaAnswer, QaIntentKind
from campusflow.domain.retrieval import RetrievalScope
from campusflow.domain.states import ReviewStatus

MAX_CONVERSATION_TURNS = 200


@dataclass(frozen=True)
class RevalidatedTurn:
    turn: QaTurnData
    citations: tuple[RevalidatedCitation, ...]


@dataclass(frozen=True)
class ConversationDetail:
    """会话读取结果：每轮引用已按当前来源状态重新核对，覆盖提示跨轮去重。"""

    conversation: QaConversationData
    turns: tuple[RevalidatedTurn, ...]
    coverage: tuple  # 合并后的覆盖提示（与轮次类型一致）
    pending_conflict_count: int


def _require_mode(mode: str) -> str:
    if mode not in {"keyword", "vector", "hybrid"}:
        raise DomainError("问答排序方式无效")
    return mode


def create_conversation(
    repos: Repositories,
    store: ConversationStore,
    workspace_id: int,
    scope: RetrievalScope,
    *,
    mode: str = "hybrid",
) -> QaConversationData:
    """创建会话前重新校验范围；空间与对象归属以已认证会话为准。"""
    validate_retrieval_scope(repos, workspace_id, scope)
    return store.create(workspace_id, scope, _require_mode(mode))


def switch_conversation_scope(
    repos: Repositories,
    store: ConversationStore,
    workspace_id: int,
    conversation_id: int,
    scope: RetrievalScope,
) -> QaConversationData:
    """切换范围必须显式调用本用例；校验失败时不改动原会话范围。"""
    validate_retrieval_scope(repos, workspace_id, scope)
    conversation = store.update_scope(workspace_id, conversation_id, scope)
    if conversation is None:
        raise NotFoundError("问答会话不存在")
    return conversation


def get_conversation(
    store: ConversationStore, workspace_id: int, conversation_id: int
) -> QaConversationData:
    conversation = store.get(workspace_id, conversation_id)
    if conversation is None:
        raise NotFoundError("问答会话不存在")
    return conversation


def record_turn(
    store: ConversationStore,
    workspace_id: int,
    conversation: QaConversationData,
    question: str,
    answer: QaAnswer,
    *,
    intent: QaIntentKind = QaIntentKind.QUERY,
) -> QaTurnData:
    """把这一轮的范围与引用快照随回答一并保存；后续读取以快照重新核对。"""
    turn = NewQaTurn(
        question=question,
        answer=answer,
        intent=intent,
        scope=conversation.scope,
    )
    return store.add_turn(workspace_id, conversation.id, turn)


def _pending_conflict_count(repos: Repositories, conversation: QaConversationData) -> int:
    """统计会话范围内仍待核对、且带来源冲突原因的候选数量。"""
    candidates = repos.candidates.list_for_review(
        conversation.workspace_id,
        review_statuses=(ReviewStatus.PENDING,),
        semester_id=conversation.scope.semester_id,
        course_id=conversation.scope.course_id,
    )
    return sum(
        1
        for candidate in candidates
        if any(code == "source_conflict" for code, _, _ in candidate.review_reasons)
    )


def read_conversation(
    repos: Repositories,
    store: ConversationStore,
    sources: CitationSources,
    workspace_id: int,
    conversation_id: int,
) -> ConversationDetail:
    """读取会话并逐轮重新核对引用；已删除或版本被替代的引用不再显示为可验证。"""
    conversation = get_conversation(store, workspace_id, conversation_id)
    turns = store.list_turns(workspace_id, conversation_id)
    # 超长会话只返回最近若干轮，避免一次读取无上限；截断不影响旧轮次的落库。
    if len(turns) > MAX_CONVERSATION_TURNS:
        turns = turns[-MAX_CONVERSATION_TURNS:]
    revalidated = []
    coverage = []
    seen_coverage = set()
    for turn in turns:
        states = sources.revalidate(workspace_id, turn.citations)
        revalidated.append(RevalidatedTurn(turn, states))
        # 同一覆盖提示在多轮重复出现时只展示一次，避免页面被重复提示淹没。
        for notice in turn.coverage:
            key = (notice.kind, notice.material_id, notice.message)
            if key in seen_coverage:
                continue
            seen_coverage.add(key)
            coverage.append(notice)
    return ConversationDetail(
        conversation=conversation,
        turns=tuple(revalidated),
        coverage=tuple(coverage),
        pending_conflict_count=_pending_conflict_count(repos, conversation),
    )


def require_citations_subset(
    workspace_id: int, conversation: QaConversationData, citations: tuple[Citation, ...]
) -> None:
    """后续追问不能把其他空间或范围的引用带进当前会话。"""
    if conversation.workspace_id != workspace_id:
        raise NotFoundError("问答会话不存在")
    for citation in citations:
        # 未标范围的引用按当前会话范围接受；明确标了其他学期或课程则拒绝。
        if citation.semester_id not in (None, conversation.scope.semester_id):
            raise DomainError("引用不属于当前会话的学期范围")
        if citation.course_id not in (None, conversation.scope.course_id):
            raise DomainError("引用不属于当前会话的课程范围")
