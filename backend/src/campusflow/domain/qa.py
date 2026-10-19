"""Q011–Q015：问答契约。

模型只输出受控 JSON；引用必须由候选集校验，覆盖范围、冲突和不确定项由代码补充。
模型正文不能变成工具调用或执行命令。
"""

import json
import re
from dataclasses import dataclass
from enum import StrEnum

from campusflow.domain.citations import Citation
from campusflow.domain.errors import ModelOutputError

MAX_ANSWER_CHARS = 8000
MAX_UNCERTAINTIES = 20
MAX_CITATIONS_PER_ANSWER = 20

_ANSWER_KEYS = {"insufficient", "answer", "citation_ids", "uncertainties"}

# 只用于提示“同一类要求的日期不一致”，不替代正式事实提取。
_DATE_PATTERNS = (
    ("deadline", re.compile(r"(?:截止|提交|交|due)[^。；\n]{0,20}?(\d{1,2})月(\d{1,2})日")),
    ("deadline", re.compile(r"(\d{1,2})月(\d{1,2})日(?:前|之前|截止|提交|交)")),
    ("deadline", re.compile(r"(\d{1,2})月(\d{1,2})日前")),
    ("exam", re.compile(r"(?:考试|测验|考核)[^。；\n]{0,20}?(\d{1,2})月(\d{1,2})日")),
    ("exam", re.compile(r"(\d{1,2})月(\d{1,2})日[^。；\n]{0,10}(?:考试|测验|考核)")),
)
_FULL_DATE = re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日")
_YEARLESS_DATE = re.compile(r"(?<!\d{4}年)(\d{1,2})月(\d{1,2})日")


class QaIntentKind(StrEnum):
    QUERY = "query"
    PLANNING = "planning"
    CONFLICT_CHECK = "conflict_check"


INTENT_BOUNDARIES = {
    QaIntentKind.PLANNING: (
        "已接通计划生成：本次只生成待确认草案，未接受、未改写当前计划。"
        "请在任务与计划页面核对后再接受。"
    ),
    QaIntentKind.CONFLICT_CHECK: (
        "已接通冲突检查：只读取当前已接受计划与固定日程，不修改任何安排。"
    ),
}

INTENT_PARAMETERS = {
    QaIntentKind.QUERY: frozenset({"semester_id", "course_id", "class_name", "time_range"}),
    QaIntentKind.PLANNING: frozenset({"semester_id", "course_id", "date_range"}),
    QaIntentKind.CONFLICT_CHECK: frozenset({"semester_id", "course_id", "date_range"}),
}


@dataclass(frozen=True)
class QaIntent:
    """已校验的意图；只保留固定枚举与白名单参数，模型不能借正文指定工具或命令。"""

    kind: QaIntentKind
    parameters: dict

    @property
    def available(self) -> bool:
        # 三类意图当前都已接通，因此恒为 True；调用方仍应读取该字段，不硬编码能力。
        return True

    @property
    def boundary(self) -> str | None:
        return INTENT_BOUNDARIES.get(self.kind)


@dataclass(frozen=True)
class GeneratedAnswer:
    """通过协议校验的模型输出；引用仍是编号，尚未绑定为候选集中的引用对象。"""

    insufficient: bool
    answer: str
    citation_ids: tuple[str, ...]
    uncertainties: tuple[str, ...]


def parse_generated_answer(raw: str) -> GeneratedAnswer:
    """只有结构完整、无额外字段的 JSON 才被接受；截断或工具调用一律拒绝。"""
    if not isinstance(raw, str) or not raw.strip():
        raise ModelOutputError("模型未返回问答正文")
    if len(raw) > MAX_ANSWER_CHARS * 2:
        raise ModelOutputError("模型返回的问答内容超过长度上限")
    try:
        data = json.loads(raw)
    except (ValueError, TypeError, RecursionError):
        raise ModelOutputError("模型未返回可解析的问答 JSON") from None
    if not isinstance(data, dict) or set(data) - _ANSWER_KEYS:
        raise ModelOutputError("模型返回了问答协议以外的字段，已拒绝")
    insufficient = data.get("insufficient", False)
    if type(insufficient) is not bool:
        raise ModelOutputError("模型返回的资料不足标记无效")
    answer = data.get("answer")
    if not isinstance(answer, str) or len(answer) > MAX_ANSWER_CHARS:
        raise ModelOutputError("模型返回的答案正文无效")
    citation_ids = data.get("citation_ids")
    if not isinstance(citation_ids, list) or any(
        not isinstance(item, str) or not item for item in citation_ids
    ):
        raise ModelOutputError("模型返回的引用编号列表无效")
    if len(citation_ids) > MAX_CITATIONS_PER_ANSWER:
        raise ModelOutputError("模型返回的引用数量超过上限")
    uncertainties = data.get("uncertainties", [])
    if not isinstance(uncertainties, list) or any(
        not isinstance(item, str) or not item.strip() or len(item) > 500 for item in uncertainties
    ):
        raise ModelOutputError("模型返回的不确定项无效")
    if len(uncertainties) > MAX_UNCERTAINTIES:
        raise ModelOutputError("模型返回的不确定项过多")
    if insufficient and citation_ids:
        raise ModelOutputError("资料不足的回答不能附带引用")
    if not insufficient and not answer.strip():
        raise ModelOutputError("模型没有给出答案正文")
    if not insufficient and not citation_ids:
        raise ModelOutputError("答案缺少原文引用，未接受无依据回答")
    return GeneratedAnswer(insufficient, answer.strip(), tuple(citation_ids), tuple(uncertainties))


def parse_intent(raw: str) -> QaIntent:
    """意图只能来自固定枚举和参数白名单，模型不能指定工具或命令。"""
    try:
        data = json.loads(raw)
    except (ValueError, TypeError, RecursionError):
        raise ModelOutputError("模型未返回可解析的意图 JSON") from None
    if not isinstance(data, dict) or set(data) != {"intent", "parameters"}:
        raise ModelOutputError("模型返回的意图协议字段无效")
    try:
        kind = QaIntentKind(data["intent"])
    except (ValueError, TypeError):
        raise ModelOutputError("模型返回了未支持的意图") from None
    parameters = data["parameters"]
    if not isinstance(parameters, dict):
        raise ModelOutputError("意图参数必须是对象")
    unknown = set(parameters) - INTENT_PARAMETERS[kind]
    if unknown:
        raise ModelOutputError("意图包含未授权参数或工具名称，已拒绝")
    clean = {}
    for key, value in parameters.items():
        if value is None:
            continue
        if key in {"semester_id", "course_id"}:
            if type(value) is not int or value <= 0:
                raise ModelOutputError("意图中的学期或课程编号无效")
        elif key in {"class_name", "time_range", "date_range"}:
            if not isinstance(value, str) or not value.strip() or len(value) > 100:
                raise ModelOutputError("意图中的范围参数无效")
            value = value.strip()
        clean[key] = value
    return QaIntent(kind, clean)


@dataclass(frozen=True)
class CoverageNotice:
    """覆盖提示：哪些资料或片段没有参与回答，必须随答案一并展示。"""

    kind: str
    material_id: int
    title: str
    message: str
    uncovered_source_count: int = 0
    failed_blocks: int = 0


@dataclass(frozen=True)
class MaterialComparison:
    """按资料归并的引用与摘录，用于多资料对照，不代替逐条引用。"""

    material_id: int
    title: str
    semester_id: int | None
    course_id: int | None
    class_name: str | None
    citation_ids: tuple[str, ...]
    excerpts: tuple[str, ...]


@dataclass(frozen=True)
class ConflictNotice:
    """引用之间的矛盾提示；requires_review 表示尚未判断哪一侧生效。"""

    code: str
    message: str
    citation_ids: tuple[str, ...]
    requires_review: bool = True


@dataclass(frozen=True)
class QaAnswer:
    """最终回答：正文、引用、覆盖、对照与冲突分开保存；引用只能来自本次候选集。"""

    answer: str
    citations: tuple[Citation, ...]
    insufficient: bool
    coverage: tuple[CoverageNotice, ...] = ()
    comparisons: tuple[MaterialComparison, ...] = ()
    conflicts: tuple[ConflictNotice, ...] = ()
    uncertainties: tuple[str, ...] = ()
    intent: QaIntentKind = QaIntentKind.QUERY


def build_comparisons(
    citations: tuple[Citation, ...] | list[Citation],
) -> tuple[MaterialComparison, ...]:
    """按资料分组引用；组内保持引用原顺序，组间按资料编号排序，保证输出稳定。"""
    groups: dict[int, list[Citation]] = {}
    for citation in citations:
        groups.setdefault(citation.material_id, []).append(citation)
    comparisons = []
    for material_id in sorted(groups):
        group = groups[material_id]
        first = group[0]
        comparisons.append(
            MaterialComparison(
                material_id=material_id,
                title=first.material_title,
                semester_id=first.semester_id,
                course_id=first.course_id,
                class_name=first.class_name,
                citation_ids=tuple(item.id for item in group),
                excerpts=tuple(item.excerpt for item in group),
            )
        )
    return tuple(comparisons)


def detect_date_conflicts(
    citations: tuple[Citation, ...] | list[Citation],
) -> tuple[ConflictNotice, ...]:
    """同一类要求出现不同日期时并列提示，不按资料新旧自动选择。"""
    by_category: dict[str, dict[str, set[str]]] = {}
    # 只比较“几月几日”，不判断年份：这是提示可能不一致，不是冲突裁决。
    for citation in citations:
        for category, pattern in _DATE_PATTERNS:
            for match in pattern.finditer(citation.excerpt):
                month, day = match.groups()
                value = f"{int(month)}月{int(day)}日"
                by_category.setdefault(category, {}).setdefault(value, set()).add(citation.id)
    conflicts = []
    labels = {"deadline": "截止日期", "exam": "考试日期"}
    for category, values in sorted(by_category.items()):
        if len(values) < 2:
            continue
        detail = "、".join(sorted(values))
        ids = tuple(sorted({citation_id for group in values.values() for citation_id in group}))
        conflicts.append(
            ConflictNotice(
                code=f"conflicting_{category}",
                message=(
                    f"引用的资料对{labels.get(category, '日期')}给出不同说法（{detail}），"
                    "不能判断哪一条生效，请核对来源版本与适用范围。"
                ),
                citation_ids=ids,
            )
        )
    return tuple(conflicts)


def incomplete_date_notes(citations: tuple[Citation, ...] | list[Citation]) -> tuple[str, ...]:
    """引文只有月日、没有完整年份时提示核对；不按发布年份或当前年份换算。"""
    for citation in citations:
        if not _YEARLESS_DATE.search(citation.excerpt):
            continue
        if _FULL_DATE.search(citation.excerpt):
            continue
        return ("原文只写了月日、没有年份；未换算为确定日期，请核对发布年份或校历。",)
    return ()


def support_warnings(
    answer: str, citations: tuple[Citation, ...] | list[Citation]
) -> tuple[str, ...]:
    """引用 ID 合法只是候选集校验；回答里的日期还必须能在被引原文中找到。"""
    if not citations:
        return ("回答没有引用原文，不能视为有依据。",)
    excerpts = "\n".join(citation.excerpt for citation in citations)
    warnings = []
    for pattern in (_FULL_DATE, _YEARLESS_DATE):
        for match in pattern.finditer(answer):
            literal = match.group(0)
            if literal not in excerpts:
                warnings.append(f"回答中的日期“{literal}”未出现在被引原文中，请核对依据。")
    return tuple(dict.fromkeys(warnings))
