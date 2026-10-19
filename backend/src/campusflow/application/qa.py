"""Q011–Q015：问答用例。

先完成授权与范围读取，结束读取事务后再调用向量/生成模型；回答中的引用必须
全部属于本次候选集。资料不足、部分覆盖与冲突分别返回，不用模型正文冒充事实。
"""

import json
from dataclasses import dataclass

from campusflow.application.ports.embeddings import Embedder
from campusflow.application.ports.generation import GenerationRequest, TextGenerator
from campusflow.application.ports.repositories import MaterialData, Repositories
from campusflow.application.ports.retrieval import RetrievalCandidates
from campusflow.application.retrieval import collect_retrieval_candidates
from campusflow.domain.citations import Citation, build_citation_set, validate_citation_ids
from campusflow.domain.errors import DomainError, ModelOutputError
from campusflow.domain.model_failures import INSUFFICIENT_MATERIALS_MESSAGE
from campusflow.domain.qa import (
    ConflictNotice,
    CoverageNotice,
    QaAnswer,
    QaIntent,
    QaIntentKind,
    build_comparisons,
    detect_date_conflicts,
    incomplete_date_notes,
    parse_generated_answer,
    parse_intent,
    support_warnings,
)
from campusflow.domain.ranking import DEFAULT_TOP_K, RankingMode, rank_candidates
from campusflow.domain.retrieval import RetrievalScope, ScopedCandidates

ANSWER_SYSTEM_PROMPT = "\n".join(
    (
        "你是 CampusFlow 的课程资料问答助手。只能使用用户消息中给出的候选原文回答。",
        "必须返回一个 JSON 对象，字段固定为：",
        '{"insufficient": false, "answer": "中文回答", "citation_ids": ["引用编号"],',
        ' "uncertainties": ["不确定项"]}',
        "规则：",
        "1. 每个事实性结论都必须引用候选编号；不得使用候选之外的引用，不得编造页码、日期或课程。",
        "2. 日期、课程名、教学班和适用范围必须与原文一致；"
        "原文缺少年份、时刻或提交方式时，在 uncertainties 中说明。",
        "3. 不同资料对同一项要求给出不同说法时，在 answer 中并列展示，不要按导入时间选择；"
        "在 uncertainties 中提示核对生效版本。",
        "4. 现有候选不足以回答时，返回 insufficient=true，answer 可以为空。"
        "不得把“资料里没写”说成“没有作业”或“没有冲突”。",
        "5. 不得输出工具调用、命令、链接或其他字段。",
    )
)

INTENT_SYSTEM_PROMPT = (
    "判断用户问题属于哪类意图。只返回 JSON："
    '{"intent": "query|planning|conflict_check", '
    '"parameters": {"semester_id": 1, "course_id": 2, "class_name": "一班", '
    '"time_range": "未来两周"}}\n'
    "只能使用这些参数名；不要输出工具名、命令或执行请求。无法判断时返回 query。"
)


@dataclass(frozen=True)
class PreparedQuestion:
    """读取阶段的结果快照：候选、覆盖范围和未索引资料。

    只在读取事务内填充；向量与生成调用必须在调用方结束读取事务后进行，
    避免持锁等待外部模型。
    """

    workspace_id: int
    scope: RetrievalScope
    question: str
    profile_id: str
    candidates: ScopedCandidates
    unindexed_materials: tuple[MaterialData, ...]

    def coverage_notices(self) -> tuple[CoverageNotice, ...]:
        """把索引进度与未索引资料转成覆盖提示；部分覆盖必须对用户可见。"""
        notices = []
        seen_materials = set()
        for version in self.candidates.versions:
            seen_materials.add(version.material_id)
            if version.progress is None:
                notices.append(
                    CoverageNotice(
                        kind="unavailable",
                        material_id=version.material_id,
                        title=version.title or f"资料 {version.material_id}",
                        message="该资料版本的索引不可用，未参与本次回答。",
                    )
                )
                continue
            progress = version.progress
            if progress.fully_searchable:
                continue
            notices.append(
                CoverageNotice(
                    kind="partial",
                    material_id=version.material_id,
                    title=version.title or f"资料 {version.material_id}",
                    message=(
                        f"该资料仅部分可检索：成功 {progress.indexed}/{progress.expected} 块，"
                        f"失败 {progress.failed} 块，跳过 {progress.skipped} 块，"
                        f"解析未覆盖 {progress.uncovered_source_count} 处。"
                    ),
                    uncovered_source_count=progress.uncovered_source_count,
                    failed_blocks=progress.failed,
                )
            )
        for material in self.unindexed_materials:
            if material.id in seen_materials:
                continue
            notices.append(
                CoverageNotice(
                    kind="unindexed",
                    material_id=material.id,
                    title=material.title,
                    message="该资料尚未完成解析或索引，未参与本次回答。",
                )
            )
        return tuple(notices)


def _require_question(question: str) -> str:
    if not isinstance(question, str) or not question.strip():
        raise DomainError("问题不能为空")
    if len(question) > 2000:
        raise DomainError("问题不能超过 2000 字")
    return question.strip()


def prepare_question(
    repos: Repositories,
    reader: RetrievalCandidates,
    workspace_id: int,
    scope: RetrievalScope,
    profile_id: str,
    question: str,
) -> PreparedQuestion:
    """授权、范围校验与候选读取；调用方必须在返回后结束读取事务。

    workspace_id 来自已认证会话，不由问题文本决定；未建立索引的资料只用于
    覆盖提示，不进入本次候选集。
    """
    question = _require_question(question)
    candidates = collect_retrieval_candidates(repos, reader, workspace_id, scope, profile_id)
    indexed = {version.material_id for version in candidates.versions}
    # 未索引资料不参与回答，但要作为覆盖率提示返回，避免把“没检索到”说成“没有”。
    materials = tuple(
        material
        for material in repos.materials.list(
            workspace_id,
            semester_id=scope.semester_id,
            course_id=scope.course_id,
        )
        if material.id not in indexed
        and (scope.class_name is None or material.class_name == scope.class_name)
    )
    return PreparedQuestion(
        workspace_id=workspace_id,
        scope=scope,
        question=question,
        profile_id=profile_id,
        candidates=candidates,
        unindexed_materials=materials,
    )


def _insufficient(prepared: PreparedQuestion) -> QaAnswer:
    """无可用候选或模型判定资料不足时统一返回固定文案，不生成回答或引用。"""
    return QaAnswer(
        answer=INSUFFICIENT_MATERIALS_MESSAGE,
        citations=(),
        insufficient=True,
        coverage=prepared.coverage_notices(),
    )


def _location_text(citation: Citation) -> str:
    location = citation.location
    parts = [location.kind, location.value]
    if location.page is not None:
        parts.append(f"第 {location.page} 页")
    if location.paragraph is not None:
        parts.append(f"第 {location.paragraph} 段")
    return " / ".join(parts)


def _source_payload(
    prepared: PreparedQuestion,
    question: str,
    citations: tuple[Citation, ...],
) -> str:
    """只把本次候选原文、范围与覆盖提示交给模型，不附带其他资料或历史轮次。"""
    return json.dumps(
        {
            "question": question,
            "scope": {
                "semester_id": prepared.scope.semester_id,
                "course_id": prepared.scope.course_id,
                "class_name": prepared.scope.class_name,
            },
            "coverage": [notice.message for notice in prepared.coverage_notices()],
            "candidates": [
                {
                    "citation_id": citation.id,
                    "material_title": citation.material_title,
                    "semester_id": citation.semester_id,
                    "course_id": citation.course_id,
                    "class_name": citation.class_name,
                    "location": _location_text(citation),
                    "text": citation.excerpt,
                }
                for citation in citations
            ],
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def answer_prepared(
    prepared: PreparedQuestion,
    embedder: Embedder,
    generator: TextGenerator,
    *,
    mode: RankingMode = RankingMode.HYBRID,
    top_k: int = DEFAULT_TOP_K,
) -> QaAnswer:
    """在读取事务结束后调用；本函数只发起显式向量/生成请求。

    候选为空、排序为空或关键词模式零命中时直接返回资料不足，不调用生成模型；
    模型返回的引用编号必须全部属于本次候选集，否则按模型输出错误拒绝整条回答。
    """
    if not prepared.candidates.candidates:
        return _insufficient(prepared)
    query_embedding = embedder.embed(prepared.question)
    ranked = rank_candidates(
        prepared.candidates.candidates,
        prepared.question,
        mode=mode,
        limit=top_k,
        query_embedding=query_embedding,
        profile=embedder.profile,
    )
    if not ranked:
        return _insufficient(prepared)
    # 关键词零命中说明现有候选没有支持依据；不能据此断言“没有作业”。
    if mode is RankingMode.KEYWORD and all(item.keyword_score == 0 for item in ranked):
        return _insufficient(prepared)

    candidates = tuple(item.candidate for item in ranked)
    citations = build_citation_set(candidates)
    generated = parse_generated_answer(
        generator.generate(
            GenerationRequest(
                system_prompt=ANSWER_SYSTEM_PROMPT,
                source_json=_source_payload(prepared, prepared.question, citations),
            )
        )
    )
    if generated.insufficient:
        return _insufficient(prepared)
    try:
        cited = validate_citation_ids(generated.citation_ids, candidates)
    except DomainError as error:
        # 引用不属于候选集属于模型输出无效，不是用户参数错误。
        raise ModelOutputError(str(error)) from None
    conflicts = detect_date_conflicts(cited)
    uncertainties = list(generated.uncertainties)
    for note in incomplete_date_notes(cited):
        if note not in uncertainties:
            uncertainties.append(note)
    # 引用编号合法只代表属于候选集；回答中的日期仍要与被引原文逐字核对。
    for warning in support_warnings(generated.answer, cited):
        if warning not in uncertainties:
            uncertainties.append(warning)
    # 冲突以提示并列保留，不按导入时间挑一条，也不改写模型正文。
    for conflict in conflicts:
        if conflict.message not in uncertainties:
            uncertainties.append(conflict.message)
    return QaAnswer(
        answer=generated.answer,
        citations=cited,
        insufficient=False,
        coverage=prepared.coverage_notices(),
        comparisons=build_comparisons(cited),
        conflicts=conflicts,
        uncertainties=tuple(uncertainties),
    )


def detect_intent(generator: TextGenerator, question: str, scope: RetrievalScope) -> QaIntent:
    """意图识别只返回结构化枚举；未上线意图由调用方给出能力边界，不执行动作。"""
    question = _require_question(question)
    return parse_intent(
        generator.generate(
            GenerationRequest(
                system_prompt=INTENT_SYSTEM_PROMPT,
                source_json=json.dumps(
                    {
                        "question": question,
                        "scope": {
                            "semester_id": scope.semester_id,
                            "course_id": scope.course_id,
                            "class_name": scope.class_name,
                        },
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            )
        )
    )


def conflict_summary(conflicts: tuple[ConflictNotice, ...]) -> str | None:
    """返回首条冲突提示供单值展示；多条冲突不在这里合并或择一。"""
    return conflicts[0].message if conflicts else None


def capability_for(intent: QaIntent) -> dict:
    """把意图与能力边界转为只读响应；不执行工具，executed 恒为 False。"""
    return {
        "intent": intent.kind.value,
        "available": intent.available,
        "boundary": intent.boundary,
        "executed": False,
        "query_kind": QaIntentKind.QUERY.value,
    }
