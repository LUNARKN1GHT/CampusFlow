"""Q011–Q017：问答 HTTP 路由。

服务端始终以会话中的工作空间与范围读取资料；模型调用前结束读取事务。
回答正文与引用、覆盖、冲突分别返回，旧会话读取时重新核对引用来源。
"""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from campusflow.api.deps import (
    get_current_workspace,
    get_embedder,
    get_repositories,
    get_session,
    get_text_generator,
)
from campusflow.application import conversations as conversation_use_cases
from campusflow.application import qa as use_cases
from campusflow.application.ports.embeddings import Embedder
from campusflow.application.ports.generation import TextGenerator
from campusflow.application.ports.repositories import Repositories, WorkspaceData
from campusflow.application.retrieval import validate_retrieval_scope
from campusflow.domain.citations import Citation, RevalidatedCitation
from campusflow.domain.conversation import QaConversationData, QaTurnData
from campusflow.domain.errors import DomainError
from campusflow.domain.model_failures import classify_model_failure
from campusflow.domain.qa import (
    ConflictNotice,
    CoverageNotice,
    MaterialComparison,
    QaAnswer,
)
from campusflow.domain.ranking import RankingMode
from campusflow.domain.retrieval import RetrievalScope
from campusflow.infrastructure.citation_sources import SqlCitationSources
from campusflow.infrastructure.conversations import SqlConversations
from campusflow.infrastructure.retrieval import SqlRetrievalCandidates

router = APIRouter(tags=["qa"])

CurrentWorkspace = Annotated[WorkspaceData, Depends(get_current_workspace)]
Repos = Annotated[Repositories, Depends(get_repositories)]
SessionDep = Annotated[Session, Depends(get_session)]
EmbedderDep = Annotated[Embedder, Depends(get_embedder)]
GeneratorDep = Annotated[TextGenerator, Depends(get_text_generator)]


class ScopeFields(BaseModel):
    model_config = ConfigDict(extra="forbid")
    semester_id: int | None = Field(default=None, ge=1)
    course_id: int | None = Field(default=None, ge=1)
    class_name: str | None = Field(default=None, min_length=1, max_length=100)

    def to_scope(self) -> RetrievalScope:
        return RetrievalScope(
            self.semester_id,
            self.course_id,
            None if self.class_name is None else self.class_name.strip(),
        )


class QueryIn(ScopeFields):
    question: str = Field(min_length=1, max_length=2000)
    mode: RankingMode = RankingMode.HYBRID


class ConversationIn(ScopeFields):
    mode: RankingMode = RankingMode.HYBRID


class MessageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=2000)


class ScopePatchIn(ScopeFields):
    pass


class CitationOut(BaseModel):
    id: str
    material_id: int
    material_title: str
    version_id: int
    version_no: int
    block_id: str
    source_chunk_id: int
    location: str
    page: int | None
    paragraph: int | None
    excerpt: str
    valid: bool = True
    status: str = "valid"
    message: str | None = None


class CoverageOut(BaseModel):
    kind: str
    material_id: int
    title: str
    message: str
    uncovered_source_count: int
    failed_blocks: int


class ComparisonOut(BaseModel):
    material_id: int
    title: str
    semester_id: int | None
    course_id: int | None
    class_name: str | None
    citation_ids: list[str]
    excerpts: list[str]


class ConflictOut(BaseModel):
    code: str
    message: str
    citation_ids: list[str]
    requires_review: bool


class AnswerOut(BaseModel):
    answer: str
    insufficient: bool
    citations: list[CitationOut]
    coverage: list[CoverageOut]
    comparisons: list[ComparisonOut]
    conflicts: list[ConflictOut]
    uncertainties: list[str]
    intent: str = "query"


class TurnOut(BaseModel):
    id: int
    question: str
    answer: str
    insufficient: bool
    intent: str
    semester_id: int | None
    course_id: int | None
    class_name: str | None
    citations: list[CitationOut]
    coverage: list[CoverageOut]
    comparisons: list[ComparisonOut]
    conflicts: list[ConflictOut]
    uncertainties: list[str]
    created_at: datetime


class ConversationOut(BaseModel):
    id: int
    semester_id: int | None
    course_id: int | None
    class_name: str | None
    mode: str
    created_at: datetime
    updated_at: datetime


class ConversationDetailOut(BaseModel):
    conversation: ConversationOut
    turns: list[TurnOut]
    coverage: list[CoverageOut]
    pending_conflict_count: int


class IntentIn(ScopeFields):
    question: str = Field(min_length=1, max_length=2000)


class IntentOut(BaseModel):
    intent: str
    available: bool
    boundary: str | None
    executed: bool
    parameters: dict


def _location(citation: Citation) -> str:
    location = citation.location
    parts = [location.kind, location.value]
    if location.page is not None:
        parts.append(f"第 {location.page} 页")
    if location.paragraph is not None:
        parts.append(f"第 {location.paragraph} 段")
    return " / ".join(parts)


def _citation_out(citation: Citation, state: RevalidatedCitation | None = None) -> CitationOut:
    valid = True if state is None else state.valid
    return CitationOut(
        id=citation.id,
        material_id=citation.material_id,
        material_title=citation.material_title,
        version_id=citation.version_id,
        version_no=citation.version_no,
        block_id=citation.block_id,
        source_chunk_id=citation.source_chunk_id,
        location=_location(citation),
        page=citation.location.page,
        paragraph=citation.location.paragraph,
        excerpt=citation.excerpt,
        valid=valid,
        status="valid" if state is None else state.status.value,
        message=None if state is None else state.message,
    )


def _coverage_out(notice: CoverageNotice) -> CoverageOut:
    return CoverageOut(
        kind=notice.kind,
        material_id=notice.material_id,
        title=notice.title,
        message=notice.message,
        uncovered_source_count=notice.uncovered_source_count,
        failed_blocks=notice.failed_blocks,
    )


def _comparison_out(item: MaterialComparison) -> ComparisonOut:
    return ComparisonOut(
        material_id=item.material_id,
        title=item.title,
        semester_id=item.semester_id,
        course_id=item.course_id,
        class_name=item.class_name,
        citation_ids=list(item.citation_ids),
        excerpts=list(item.excerpts),
    )


def _conflict_out(item: ConflictNotice) -> ConflictOut:
    return ConflictOut(
        code=item.code,
        message=item.message,
        citation_ids=list(item.citation_ids),
        requires_review=item.requires_review,
    )


def _answer_out(answer: QaAnswer, *, intent: str = "query") -> AnswerOut:
    return AnswerOut(
        answer=answer.answer,
        insufficient=answer.insufficient,
        citations=[_citation_out(item) for item in answer.citations],
        coverage=[_coverage_out(item) for item in answer.coverage],
        comparisons=[_comparison_out(item) for item in answer.comparisons],
        conflicts=[_conflict_out(item) for item in answer.conflicts],
        uncertainties=list(answer.uncertainties),
        intent=intent,
    )


def _conversation_out(conversation: QaConversationData) -> ConversationOut:
    return ConversationOut(
        id=conversation.id,
        semester_id=conversation.scope.semester_id,
        course_id=conversation.scope.course_id,
        class_name=conversation.scope.class_name,
        mode=conversation.mode,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


def _turn_out(turn: QaTurnData, states: tuple[RevalidatedCitation, ...]) -> TurnOut:
    by_id = {state.citation.id: state for state in states}
    return TurnOut(
        id=turn.id,
        question=turn.question,
        answer=turn.answer,
        insufficient=turn.insufficient,
        intent=turn.intent.value,
        semester_id=turn.scope.semester_id,
        course_id=turn.scope.course_id,
        class_name=turn.scope.class_name,
        citations=[_citation_out(item, by_id.get(item.id)) for item in turn.citations],
        coverage=[_coverage_out(item) for item in turn.coverage],
        comparisons=[_comparison_out(item) for item in turn.comparisons],
        conflicts=[_conflict_out(item) for item in turn.conflicts],
        uncertainties=list(turn.uncertainties),
        created_at=turn.created_at,
    )


def _failure_response(error: DomainError) -> JSONResponse:
    failure = classify_model_failure(error)
    if failure is None:
        raise error
    return JSONResponse(
        status_code=503 if failure.retryable else 502,
        content={"detail": failure.message, "failure_kind": failure.kind.value},
    )


def _prepare(
    repos: Repositories,
    session,
    workspace: WorkspaceData,
    scope: RetrievalScope,
    question: str,
    embedder: Embedder,
):
    return use_cases.prepare_question(
        repos,
        SqlRetrievalCandidates(session),
        workspace.id,
        scope,
        embedder.profile.id,
        question,
    )


@router.post("/qa/query", response_model=AnswerOut)
def query(
    payload: QueryIn,
    workspace: CurrentWorkspace,
    repos: Repos,
    session: SessionDep,
    embedder: EmbedderDep,
    generator: GeneratorDep,
):
    prepared = _prepare(repos, session, workspace, payload.to_scope(), payload.question, embedder)
    # Q005：读取事务在外部模型调用前结束，不能持锁等待生成。
    session.commit()
    try:
        answer = use_cases.answer_prepared(prepared, embedder, generator, mode=payload.mode)
    except DomainError as error:
        return _failure_response(error)
    return _answer_out(answer)


@router.post("/qa/intent", response_model=IntentOut)
def interpret_intent(
    payload: IntentIn,
    workspace: CurrentWorkspace,
    repos: Repos,
    session: SessionDep,
    generator: GeneratorDep,
) -> IntentOut:
    scope = payload.to_scope()
    validate_retrieval_scope(repos, workspace.id, scope)
    session.commit()
    try:
        intent = use_cases.detect_intent(generator, payload.question, scope)
    except DomainError as error:
        return _failure_response(error)
    return IntentOut(
        intent=intent.kind.value,
        available=intent.available,
        boundary=intent.boundary,
        executed=False,
        parameters=intent.parameters,
    )


@router.get("/qa/conversations", response_model=list[ConversationOut])
def list_conversations(
    workspace: CurrentWorkspace,
    session: SessionDep,
) -> list[ConversationOut]:
    store = SqlConversations(session)
    return [_conversation_out(item) for item in store.list(workspace.id)]


@router.post("/qa/conversations", response_model=ConversationOut)
def create_conversation(
    payload: ConversationIn,
    workspace: CurrentWorkspace,
    repos: Repos,
    session: SessionDep,
) -> ConversationOut:
    conversation = conversation_use_cases.create_conversation(
        repos,
        SqlConversations(session),
        workspace.id,
        payload.to_scope(),
        mode=payload.mode.value,
    )
    session.commit()
    return _conversation_out(conversation)


@router.patch("/qa/conversations/{conversation_id}/scope", response_model=ConversationOut)
def switch_scope(
    conversation_id: int,
    payload: ScopePatchIn,
    workspace: CurrentWorkspace,
    repos: Repos,
    session: SessionDep,
) -> ConversationOut:
    conversation = conversation_use_cases.switch_conversation_scope(
        repos,
        SqlConversations(session),
        workspace.id,
        conversation_id,
        payload.to_scope(),
    )
    session.commit()
    return _conversation_out(conversation)


@router.post("/qa/conversations/{conversation_id}/messages", response_model=AnswerOut)
def ask_in_conversation(
    conversation_id: int,
    payload: MessageIn,
    workspace: CurrentWorkspace,
    repos: Repos,
    session: SessionDep,
    embedder: EmbedderDep,
    generator: GeneratorDep,
):
    conversation = conversation_use_cases.get_conversation(
        SqlConversations(session), workspace.id, conversation_id
    )
    prepared = _prepare(repos, session, workspace, conversation.scope, payload.question, embedder)
    session.commit()
    try:
        answer = use_cases.answer_prepared(
            prepared,
            embedder,
            generator,
            mode=RankingMode(conversation.mode),
        )
    except DomainError as error:
        return _failure_response(error)
    conversation_use_cases.record_turn(
        SqlConversations(session), workspace.id, conversation, payload.question, answer
    )
    session.commit()
    return _answer_out(answer)


@router.get("/qa/conversations/{conversation_id}", response_model=ConversationDetailOut)
def get_conversation(
    conversation_id: int,
    workspace: CurrentWorkspace,
    repos: Repos,
    session: SessionDep,
) -> ConversationDetailOut:
    detail = conversation_use_cases.read_conversation(
        repos,
        SqlConversations(session),
        SqlCitationSources(session),
        workspace.id,
        conversation_id,
    )
    return ConversationDetailOut(
        conversation=_conversation_out(detail.conversation),
        turns=[_turn_out(item.turn, item.citations) for item in detail.turns],
        coverage=[_coverage_out(item) for item in detail.coverage],
        pending_conflict_count=detail.pending_conflict_count,
    )
