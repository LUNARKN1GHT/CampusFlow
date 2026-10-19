"""E81：课程关系图、环/缺项/矛盾、拓扑层次、下一步与学期辅助。

纯业务模块，不依赖数据库。只有已确认的正式关系进入图；强制先修参与排序，
并修约束同一学期，建议先学只作提示。任何缺失或矛盾都会阻止“图完整”的结论。
"""

from collections import defaultdict
from dataclasses import dataclass, field

from campusflow.domain.course_paths import RelationType, StudyStatus

_SEASON_ORDER = ("fall", "spring")


@dataclass(frozen=True)
class GraphCourse:
    id: int
    name: str
    code: str | None
    credits: float | None
    offered_terms: tuple[str, ...]
    study_status: str
    record_count: int


@dataclass(frozen=True)
class GraphRelation:
    id: int
    from_course_id: int
    to_course_id: int
    relation_type: RelationType
    condition_text: str | None
    source_candidate_id: int | None


@dataclass(frozen=True)
class GraphProblem:
    code: str
    message: str
    course_ids: tuple[int, ...] = ()
    relation_ids: tuple[int, ...] = ()
    path: tuple[int, ...] = ()
    candidate_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class GraphResult:
    courses: tuple[GraphCourse, ...]
    relations: tuple[GraphRelation, ...]
    cycles: tuple[GraphProblem, ...]
    conflicts: tuple[GraphProblem, ...]
    missing: tuple[GraphProblem, ...]
    pending: tuple[GraphProblem, ...]
    complete: bool
    limitations: tuple[str, ...]


@dataclass(frozen=True)
class TopoResult:
    levels: tuple[tuple[int, ...], ...]
    alternatives: bool
    problems: tuple[GraphProblem, ...]


@dataclass(frozen=True)
class NextCourse:
    course_id: int
    name: str
    study_status: str
    available: bool
    unmet: tuple[str, ...]
    recommended: tuple[str, ...]


@dataclass(frozen=True)
class SuggestedTerm:
    index: int
    season: str
    course_ids: tuple[int, ...]
    credits_known: float
    credits_unknown: int
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class SemesterSuggestion:
    terms: tuple[SuggestedTerm, ...]
    unplaced: tuple[dict, ...]
    limitations: tuple[str, ...]
    graduation_audit: bool = False
    assumptions: tuple[str, ...] = field(default_factory=tuple)


def _prerequisite_adjacency(
    course_ids: set[int], relations: tuple[GraphRelation, ...]
) -> dict[int, list[GraphRelation]]:
    adjacency: dict[int, list[GraphRelation]] = defaultdict(list)
    for relation in relations:
        if (
            relation.relation_type is RelationType.PREREQUISITE
            and relation.from_course_id in course_ids
            and relation.to_course_id in course_ids
        ):
            adjacency[relation.from_course_id].append(relation)
    return adjacency


def find_cycles(
    courses: tuple[GraphCourse, ...], relations: tuple[GraphRelation, ...]
) -> tuple[GraphProblem, ...]:
    """返回强制先修环的具体路径，包含自环；按边集合去重。"""
    course_ids = {course.id for course in courses}
    adjacency = _prerequisite_adjacency(course_ids, relations)
    color = {course_id: 0 for course_id in course_ids}
    node_stack: list[int] = []
    edge_stack: list[GraphRelation] = []
    found: list[GraphProblem] = []
    seen: set[tuple[frozenset[int], frozenset[int]]] = set()

    def visit(node: int) -> None:
        color[node] = 1
        node_stack.append(node)
        for relation in adjacency.get(node, []):
            target = relation.to_course_id
            if color.get(target) == 0:
                edge_stack.append(relation)
                visit(target)
                edge_stack.pop()
            elif color.get(target) == 1:
                start = node_stack.index(target)
                path = tuple(node_stack[start:] + [target])
                edge_ids = tuple(item.id for item in edge_stack[start:]) + (relation.id,)
                key = (frozenset(path), frozenset(edge_ids))
                if key not in seen:
                    seen.add(key)
                    found.append(
                        GraphProblem(
                            "prerequisite_cycle",
                            "强制先修关系成环，不能输出完整学习顺序",
                            path,
                            edge_ids,
                            path,
                        )
                    )
        node_stack.pop()
        color[node] = 2

    for course_id in sorted(course_ids):
        if color[course_id] == 0:
            visit(course_id)
    return tuple(found)


def find_conflicts(relations: tuple[GraphRelation, ...]) -> tuple[GraphProblem, ...]:
    """同一对课程被同时断言为互斥类型时保留两侧证据，不自动择一。"""
    by_pair: dict[tuple[int, int], list[GraphRelation]] = defaultdict(list)
    for relation in relations:
        by_pair[(relation.from_course_id, relation.to_course_id)].append(relation)
    conflicts = []
    for (source, target), items in sorted(by_pair.items()):
        types = {item.relation_type for item in items}
        inconsistent = (
            RelationType.PREREQUISITE in types and RelationType.RECOMMENDED in types
            or RelationType.PREREQUISITE in types and RelationType.COREQUISITE in types
            or RelationType.COREQUISITE in types and RelationType.RECOMMENDED in types
        )
        if inconsistent:
            conflicts.append(
                GraphProblem(
                    "relation_conflict",
                    "同一课程对存在互相矛盾的关系类型，核对前不能宣称依赖图完整",
                    (source, target),
                    tuple(item.id for item in items),
                )
            )
    return tuple(conflicts)


def topological_levels(
    courses: tuple[GraphCourse, ...], relations: tuple[GraphRelation, ...]
) -> TopoResult:
    """只按强制先修做分层；存在环时返回空层次，不伪造完整顺序。"""
    cycles = find_cycles(courses, relations)
    if cycles:
        return TopoResult((), False, cycles)
    course_ids = {course.id for course in courses}
    indegree = {course_id: 0 for course_id in course_ids}
    adjacency: dict[int, list[int]] = defaultdict(list)
    for relation in relations:
        if (
            relation.relation_type is RelationType.PREREQUISITE
            and relation.from_course_id in course_ids
            and relation.to_course_id in course_ids
        ):
            adjacency[relation.from_course_id].append(relation.to_course_id)
            indegree[relation.to_course_id] += 1
    remaining = sorted(course_id for course_id, degree in indegree.items() if degree == 0)
    levels: list[tuple[int, ...]] = []
    visited = 0
    while remaining:
        level = tuple(remaining)
        levels.append(level)
        visited += len(level)
        next_remaining: list[int] = []
        for course_id in level:
            for target in adjacency.get(course_id, []):
                indegree[target] -= 1
                if indegree[target] == 0:
                    next_remaining.append(target)
        remaining = sorted(next_remaining)
    if visited != len(course_ids):
        # 理论上已被环检测覆盖；保留防御性阻塞，不输出残缺层次。
        return TopoResult(
            (),
            False,
            (GraphProblem("prerequisite_cycle", "强制先修图仍含未消解环"),),
        )
    return TopoResult(tuple(levels), any(len(level) > 1 for level in levels), ())


def next_courses(
    courses: tuple[GraphCourse, ...],
    relations: tuple[GraphRelation, ...],
) -> tuple[NextCourse, ...]:
    """根据已修/在修状态判断下一步；建议先学单独返回，不进入硬阻塞。"""
    status = {course.id: course.study_status for course in courses}
    completed = {
        course_id for course_id, value in status.items() if value == StudyStatus.COMPLETED.value
    }
    in_progress = {
        course_id for course_id, value in status.items() if value == StudyStatus.IN_PROGRESS.value
    }
    name = {course.id: course.name for course in courses}
    result: list[NextCourse] = []
    for course in courses:
        if course.study_status == StudyStatus.COMPLETED.value:
            result.append(NextCourse(course.id, course.name, course.study_status, False, (), ()))
            continue
        unmet: list[str] = []
        recommended: list[str] = []
        for relation in relations:
            if relation.to_course_id != course.id:
                continue
            source = relation.from_course_id
            source_name = name.get(source, f"课程 #{source}")
            if relation.relation_type is RelationType.PREREQUISITE:
                if source not in completed:
                    unmet.append(f"先修课程“{source_name}”尚未完成")
            elif relation.relation_type is RelationType.COREQUISITE:
                if source not in completed and source not in in_progress:
                    unmet.append(f"需与“{source_name}”同时修读或在修")
            else:
                if source not in completed:
                    recommended.append(f"建议先完成“{source_name}”")
        result.append(
            NextCourse(
                course.id,
                course.name,
                course.study_status,
                not unmet,
                tuple(unmet),
                tuple(recommended),
            )
        )
    return tuple(result)


def _term_season(index: int, start_season: str) -> str:
    start = start_season if start_season in _SEASON_ORDER else "fall"
    return _SEASON_ORDER[(_SEASON_ORDER.index(start) + index) % len(_SEASON_ORDER)]


def suggest_terms(
    courses: tuple[GraphCourse, ...],
    relations: tuple[GraphRelation, ...],
    *,
    credit_limit: int | None,
    start_season: str = "fall",
    max_terms: int | None = None,
) -> SemesterSuggestion:
    """按拓扑层次、已知开课季节与学分上限给出学期建议；缺失规则时降级。"""
    topo = topological_levels(courses, relations)
    limitations = [
        "只依据强制先修、已知开课学期与学分上限，不能替代选课或毕业资格审核。",
        "并修只尝试安排在同一学期；建议先学不参与硬约束。",
    ]
    if not topo.levels:
        return SemesterSuggestion(
            (),
            (),
            tuple(limitations + ["存在强制先修环，未生成学期方案。"]),
            assumptions=(f"起始季节按 {start_season} 处理。",),
        )
    active = [
        course
        for course in courses
        if course.study_status
        not in (StudyStatus.COMPLETED.value, StudyStatus.IN_PROGRESS.value)
    ]
    if not active:
        return SemesterSuggestion(
            (),
            (),
            tuple(limitations + ["没有需要安排的课程（已完成或在修）。"]),
            assumptions=(f"起始季节按 {start_season} 处理。",),
        )
    level_by_id = {
        course_id: index for index, level in enumerate(topo.levels) for course_id in level
    }
    by_id = {course.id: course for course in active}
    if credit_limit is None:
        limitations.append("每学期学分上限未知，未检查学分负荷上限。")
    terms: list[dict] = []
    unplaced: list[dict] = []
    corequisite_partners: dict[int, set[int]] = defaultdict(set)
    for relation in relations:
        if relation.relation_type is RelationType.COREQUISITE:
            corequisite_partners[relation.to_course_id].add(relation.from_course_id)
            corequisite_partners[relation.from_course_id].add(relation.to_course_id)

    def term_fits(index: int, course: GraphCourse) -> tuple[bool, str | None]:
        season = _term_season(index, start_season)
        if course.offered_terms and season not in course.offered_terms:
            return False, f"已知开课学期不包含{season}"
        if course.credits is not None and credit_limit is not None:
            known = sum(
                by_id[item].credits or 0
                for item in terms[index]["courses"]
                if by_id[item].credits is not None
            )
            if known + course.credits > credit_limit:
                return False, "超过每学期学分上限"
        return True, None

    max_count = max_terms or max(4, len(topo.levels) + 2)
    position: dict[int, int] = {}
    for course in sorted(active, key=lambda item: (level_by_id.get(item.id, 0), item.name)):
        earliest = level_by_id.get(course.id, 0)
        # 前置课程因开课季节被推后时，后续课程不能排到它前面。
        for relation in relations:
            if (
                relation.relation_type is RelationType.PREREQUISITE
                and relation.to_course_id == course.id
                and relation.from_course_id in position
            ):
                earliest = max(earliest, position[relation.from_course_id] + 1)
        placed = False
        last_reason = None
        for index in range(earliest, max_count):
            while len(terms) <= index:
                terms.append(
                    {
                        "index": index,
                        "season": _term_season(index, start_season),
                        "courses": [],
                        "warnings": [],
                    }
                )
            fits, reason = term_fits(index, course)
            if not fits:
                last_reason = reason
                continue
            terms[index]["courses"].append(course.id)
            position[course.id] = index
            placed = True
            break
        if not placed:
            unplaced.append(
                {
                    "course_id": course.id,
                    "name": course.name,
                    "reason": last_reason or "在给定学期范围内无法安排",
                }
            )
    warnings_by_term: dict[int, list[str]] = defaultdict(list)
    for course_id, partners in corequisite_partners.items():
        if course_id not in position:
            continue
        for partner in partners:
            if partner in position and position[partner] != position[course_id]:
                warnings_by_term[position[course_id]].append(
                    f"并修课程“{by_id[course_id].name}”与“{by_id[partner].name}”未能安排在同一学期"
                )
    output_terms = []
    for term in terms:
        if not term["courses"]:
            continue
        known = sum(
            by_id[item].credits or 0
            for item in term["courses"]
            if by_id[item].credits is not None
        )
        unknown = sum(1 for item in term["courses"] if by_id[item].credits is None)
        if unknown:
            term["warnings"].append(f"{unknown} 门课程学分未知，未计入上限判断")
        output_terms.append(
            SuggestedTerm(
                term["index"],
                term["season"],
                tuple(sorted(term["courses"], key=lambda item: by_id[item].name)),
                known,
                unknown,
                tuple(dict.fromkeys(term["warnings"] + warnings_by_term.get(term["index"], []))),
            )
        )
    if unplaced:
        limitations.append("存在未能排入的课程，未隐藏或强行生成完整方案。")
    return SemesterSuggestion(
        tuple(output_terms),
        tuple(unplaced),
        tuple(limitations),
        assumptions=(
            f"起始季节按 {start_season} 处理。",
            "学分未知的课程保留未知状态，不按常识补值。",
        ),
    )
