"""任务用例：编排校验、仓储与事务。

核对状态（M2 的 ExtractedItem）与任务执行进度相互独立；
手动任务只维护执行进度。update_task 接收"合并后的最终值"（由 API 层
把补丁与当前值合并后传入），None 表示该字段确实要清空。
所有按 ID 的操作校验对象归属当前空间（W006）；创建、更新与进度变更会在同一事务内
同步提醒，逾期标记按工作空间时区派生，不写入任务记录。
"""

from dataclasses import dataclass, replace
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from campusflow.application.courses import require_semester_in_workspace
from campusflow.application.ports.repositories import Repositories, TaskData, TaskDependencyData
from campusflow.application.reminders import sync_reminders
from campusflow.application.scope import require_in_workspace
from campusflow.domain.dates import is_overdue, validate_due
from campusflow.domain.errors import DomainError, NotFoundError
from campusflow.domain.states import DueSourceType, TaskPriority, TaskProgress
from campusflow.domain.task_dependencies import cyclic_components, dependency_cycle


@dataclass(frozen=True)
class SubtaskDraft:
    """用户确认后用于批量创建子任务的可编辑草案（A004）。"""

    title: str
    priority: TaskPriority = TaskPriority.MEDIUM
    estimated_minutes: int | None = None
    remaining_minutes: int | None = None
    due_date: date | None = None


def _apply_overdue(repos: Repositories, workspace_id: int, tasks: list[TaskData]) -> list[TaskData]:
    workspace = repos.workspaces.get(workspace_id)
    if workspace is None:
        return tasks
    # 逾期由截止时间与完成情况实时派生，不落库；仅日期 DDL 在当天内不算逾期。
    now = datetime.now(ZoneInfo(workspace.timezone))
    return [
        replace(task, overdue=is_overdue(task.due_date, task.due_time, task.progress, now))
        for task in tasks
    ]


def list_tasks(
    repos: Repositories,
    workspace_id: int,
    *,
    course_id: int | None = None,
    progress: TaskProgress | None = None,
    semester_id: int | None = None,
    due_from: date | None = None,
    due_to: date | None = None,
) -> list[TaskData]:
    """按课程、进度、学期或截止区间筛选任务，并补上按空间时区派生的逾期标记。

    按学期筛选只返回关联该学期课程的任务；未关联课程的个人任务仅在未指定学期时出现。
    学期归档不改变任务查询结果，历史记录仍可读取。
    """
    if semester_id is not None:
        require_semester_in_workspace(repos, workspace_id, semester_id)
    if due_from is not None and due_to is not None and due_from > due_to:
        raise DomainError("截止区间起点不能晚于终点")
    tasks = repos.tasks.list(
        workspace_id,
        course_id=course_id,
        progress=progress,
        semester_id=semester_id,
        due_from=due_from,
        due_to=due_to,
    )
    return _apply_overdue(repos, workspace_id, tasks)


def _require_task_in_workspace(repos: Repositories, workspace_id: int, task_id: int) -> TaskData:
    task = repos.tasks.get(task_id)
    if task is None:
        raise NotFoundError("任务不存在")
    require_in_workspace(task.workspace_id, workspace_id, "任务不存在")
    return task


def _require_course_in_workspace(repos: Repositories, workspace_id: int, course_id: int) -> None:
    course = repos.courses.get(course_id)
    if course is None:
        raise NotFoundError("课程不存在")
    require_in_workspace(course.workspace_id, workspace_id, "课程不存在")


def get_task(repos: Repositories, workspace_id: int, task_id: int) -> TaskData:
    task = _require_task_in_workspace(repos, workspace_id, task_id)
    return _apply_overdue(repos, workspace_id, [task])[0]


def create_task(
    repos: Repositories,
    workspace_id: int,
    *,
    course_id: int | None,
    title: str,
    description: str | None,
    due_date: date | None,
    due_time: time | None,
    target_date: date | None,
    due_source: DueSourceType,
    due_note: str | None,
    priority: TaskPriority,
    estimated_minutes: int | None,
    remaining_minutes: int | None,
    parent_task_id: int | None = None,
) -> TaskData:
    """创建任务，并在同一事务内生成对应提醒（A013）。

    指定父任务时只建立一层包含关系，不创建先后依赖；未指定课程则继承父任务课程。
    只有日期的截止保持日期精度，不补造具体时刻。
    """
    error = validate_due(due_date, due_time)
    if error:
        raise DomainError(error)
    if parent_task_id is not None:
        parent = _require_task_in_workspace(repos, workspace_id, parent_task_id)
        if parent.parent_task_id is not None:
            raise DomainError("仅支持一层子任务；请先把任务调整为顶层任务再拆分")
        if course_id is None:
            course_id = parent.course_id
    if course_id is not None:
        _require_course_in_workspace(repos, workspace_id, course_id)
    task = repos.tasks.create(
        workspace_id,
        course_id,
        title,
        description,
        due_date,
        due_time,
        target_date,
        due_source,
        due_note,
        priority,
        estimated_minutes,
        remaining_minutes,
        parent_task_id=parent_task_id,
    )
    sync_reminders(repos, workspace_id, task_ids=[task.id])
    repos.uow.commit()
    return _apply_overdue(repos, workspace_id, [task])[0]


def update_task(
    repos: Repositories,
    workspace_id: int,
    task_id: int,
    *,
    course_id: int | None,
    title: str,
    description: str | None,
    due_date: date | None,
    due_time: time | None,
    target_date: date | None,
    due_source: DueSourceType,
    due_note: str | None,
    priority: TaskPriority,
    estimated_minutes: int | None,
    remaining_minutes: int | None,
) -> TaskData:
    """写入任务的合并后最终值，并同步提醒；None 表示清空该字段而非保持不变。"""
    _require_task_in_workspace(repos, workspace_id, task_id)
    error = validate_due(due_date, due_time)
    if error:
        raise DomainError(error)
    if course_id is not None:
        _require_course_in_workspace(repos, workspace_id, course_id)
    updated = repos.tasks.update(
        task_id,
        course_id=course_id,
        title=title,
        description=description,
        due_date=due_date,
        due_time=due_time,
        target_date=target_date,
        due_source=due_source,
        due_note=due_note,
        priority=priority,
        estimated_minutes=estimated_minutes,
        remaining_minutes=remaining_minutes,
    )
    sync_reminders(repos, workspace_id, task_ids=[task_id])
    repos.uow.commit()
    return _apply_overdue(repos, workspace_id, [updated])[0]


def set_progress(
    repos: Repositories, workspace_id: int, task_id: int, progress: TaskProgress, reason: str | None
) -> TaskData:
    """更新执行进度并追加进度历史，不触碰候选事项的核对状态。

    完成或取消会把剩余耗时归零并撤消待发送提醒；重新打开按预计耗时恢复剩余耗时，
    提醒同步可恢复仍未失效的已取消记录，但不会重发已读或已忽略的提醒。
    """
    _require_task_in_workspace(repos, workspace_id, task_id)
    task = repos.tasks.set_progress(task_id, progress, reason)
    sync_reminders(repos, workspace_id, task_ids=[task_id])
    repos.uow.commit()
    return _apply_overdue(repos, workspace_id, [task])[0]


def list_progress_changes(repos: Repositories, workspace_id: int, task_id: int):
    _require_task_in_workspace(repos, workspace_id, task_id)
    return repos.tasks.list_progress_changes(task_id)


def set_parent(
    repos: Repositories, workspace_id: int, task_id: int, parent_task_id: int | None
) -> TaskData:
    """设置或清除子任务的父任务（A001）。

    只改包含关系，不创建先后依赖；父链上出现自己时拒绝，避免包含关系成环。
    """
    task = _require_task_in_workspace(repos, workspace_id, task_id)
    if parent_task_id is not None:
        parent = _require_task_in_workspace(repos, workspace_id, parent_task_id)
        current = parent
        while current is not None:
            if current.id == task.id:
                raise DomainError("不能把任务设为自己的子任务或其子任务下，包含关系不能成环")
            current = (
                repos.tasks.get(current.parent_task_id)
                if current.parent_task_id is not None
                else None
            )
        if parent.parent_task_id is not None:
            raise DomainError("仅支持一层子任务；目标父任务本身也是子任务")
        if repos.tasks.list_subtasks(task.id):
            raise DomainError("该任务已有子任务，不能再设为其他任务的子任务")
    updated = repos.tasks.set_parent(task_id, parent_task_id)
    repos.uow.commit()
    return _apply_overdue(repos, workspace_id, [updated])[0]


def _task_ref(task: TaskData) -> dict:
    return {
        "id": task.id,
        "title": task.title,
        "progress": task.progress,
        "due_date": task.due_date,
        "parent_task_id": task.parent_task_id,
    }


def task_relations(repos: Repositories, workspace_id: int, task_id: int) -> dict:
    """任务包含关系与先后依赖（A001），两者分字段返回，不互相推断。"""
    task = _require_task_in_workspace(repos, workspace_id, task_id)
    parent = repos.tasks.get(task.parent_task_id) if task.parent_task_id is not None else None
    subtasks = repos.tasks.list_subtasks(task.id)
    prerequisites = [
        repos.tasks.get(link.depends_on_task_id) for link in repos.tasks.list_dependencies(task.id)
    ]
    dependents = [repos.tasks.get(link.task_id) for link in repos.tasks.list_dependents(task.id)]
    return {
        "task_id": task.id,
        "parent": _task_ref(parent) if parent is not None else None,
        "subtasks": [_task_ref(item) for item in subtasks],
        "prerequisites": [_task_ref(item) for item in prerequisites if item is not None],
        "dependents": [_task_ref(item) for item in dependents if item is not None],
        "rollup": subtask_rollup(repos, workspace_id, task.id),
    }


def subtask_rollup(repos: Repositories, workspace_id: int, task_id: int) -> dict:
    """父任务直接子任务的进度与耗时汇总（A004）。

    只读取子任务，不写回父任务，也不把汇总值当成新的耗时事实。
    """
    _require_task_in_workspace(repos, workspace_id, task_id)
    children = repos.tasks.list_subtasks(task_id)
    estimated = sum(
        child.estimated_minutes
        if child.estimated_minutes is not None
        else (child.remaining_minutes or 0)
        for child in children
    )
    remaining = sum(
        0
        if child.progress in (TaskProgress.DONE, TaskProgress.CANCELLED)
        else (child.remaining_minutes or 0)
        for child in children
    )
    progress_counts: dict[str, int] = {}
    for child in children:
        progress_counts[child.progress.value] = progress_counts.get(child.progress.value, 0) + 1
    percent_complete = None
    if estimated > 0:
        percent_complete = round(max(0, estimated - remaining) * 100 / estimated)
    return {
        "subtask_count": len(children),
        "estimated_minutes": estimated,
        "remaining_minutes": remaining,
        "missing_duration_count": sum(1 for child in children if child.remaining_minutes is None),
        "progress_counts": progress_counts,
        "percent_complete": percent_complete,
    }


def create_subtasks(
    repos: Repositories,
    workspace_id: int,
    parent_task_id: int,
    drafts: list[SubtaskDraft],
) -> list[TaskData]:
    """在父任务下批量创建可编辑子任务（A004）；子任务保留父任务与课程关联。"""
    parent = _require_task_in_workspace(repos, workspace_id, parent_task_id)
    if parent.parent_task_id is not None:
        raise DomainError("仅支持一层子任务；请先把任务调整为顶层任务再拆分")
    if not drafts:
        raise DomainError("至少需要一项子任务")
    for draft in drafts:
        if not draft.title.strip():
            raise DomainError("子任务标题不能为空")
        error = validate_due(draft.due_date, None)
        if error:
            raise DomainError(error)
        if draft.estimated_minutes is not None and draft.estimated_minutes <= 0:
            raise DomainError("子任务预计耗时必须大于 0 分钟")
        if draft.remaining_minutes is not None and draft.remaining_minutes < 0:
            raise DomainError("子任务剩余耗时不能为负数")

    # 预计与剩余耗时互相兜底，避免只填一侧的子任务被当成耗时未知。
    created = [
        repos.tasks.create(
            workspace_id,
            parent.course_id,
            draft.title.strip(),
            None,
            draft.due_date,
            None,
            None,
            DueSourceType.USER,
            None,
            draft.priority,
            (
                draft.estimated_minutes
                if draft.estimated_minutes is not None
                else draft.remaining_minutes
            ),
            (
                draft.remaining_minutes
                if draft.remaining_minutes is not None
                else draft.estimated_minutes
            ),
            parent_task_id=parent_task_id,
        )
        for draft in drafts
    ]
    repos.uow.commit()
    return _apply_overdue(repos, workspace_id, created)


def split_suggestions(
    repos: Repositories,
    workspace_id: int,
    task_id: int,
    *,
    titles: list[str] | None = None,
) -> dict:
    """生成可编辑的拆分为子任务建议（A004）；只返回草案，不写库。"""
    task = _require_task_in_workspace(repos, workspace_id, task_id)
    if task.parent_task_id is not None:
        raise DomainError("子任务不再拆分；请调整任务层级后再试")
    names = [title.strip() for title in (titles or []) if title.strip()]
    if not names:
        names = [
            f"阅读与准备《{task.title}》",
            f"完成《{task.title}》主体",
            f"检查并提交《{task.title}》",
        ]
    if len(names) < 2:
        raise DomainError("拆分建议至少需要两个子任务")

    total = task.remaining_minutes if task.remaining_minutes is not None else task.estimated_minutes
    if total is None:
        amounts: list[int | None] = [None] * len(names)
        note = "父任务缺少剩余耗时，建议先补充耗时再按建议拆分"
    else:
        base, extra = divmod(total, len(names))
        amounts = [base + (1 if index < extra else 0) for index in range(len(names))]
        note = "以下为可编辑建议，确认后再创建子任务；父任务耗时不与子任务重复计入"
    existing = len(repos.tasks.list_subtasks(task_id))
    if existing:
        note = f"父任务已有 {existing} 个子任务；{note}"
    return {
        "parent_task_id": task.id,
        "parent_title": task.title,
        "suggestion": True,
        "editable": True,
        "note": note,
        "items": [
            {
                "title": title,
                "estimated_minutes": amount,
                "remaining_minutes": amount,
                "suggested": True,
            }
            for title, amount in zip(names, amounts, strict=True)
        ],
    }


def _existing_dependency(
    repos: Repositories, task_id: int, depends_on_task_id: int
) -> TaskDependencyData | None:
    for link in repos.tasks.list_dependencies(task_id):
        if link.depends_on_task_id == depends_on_task_id:
            return link
    return None


def _dependency_edges(repos: Repositories, workspace_id: int) -> dict[int, set[int]]:
    edges: dict[int, set[int]] = {}
    for link in repos.tasks.list_workspace_dependencies(workspace_id):
        edges.setdefault(link.task_id, set()).add(link.depends_on_task_id)
    return edges


def _dependency_titles(repos: Repositories, task_ids: set[int]) -> dict[int, str]:
    titles: dict[int, str] = {}
    for task_id in task_ids:
        task = repos.tasks.get(task_id)
        titles[task_id] = task.title if task is not None else f"任务 {task_id}"
    return titles


def add_dependency(
    repos: Repositories, workspace_id: int, task_id: int, depends_on_task_id: int
) -> TaskDependencyData:
    """添加"先完成前置任务"的必要依赖（A001）；成环时拒绝（A002）。

    重复添加保持幂等；环内任务不能被当作可执行顺序，因此先校验再加边。
    """
    _require_task_in_workspace(repos, workspace_id, task_id)
    _require_task_in_workspace(repos, workspace_id, depends_on_task_id)
    if task_id == depends_on_task_id:
        raise DomainError("任务不能依赖自己")
    existing = _existing_dependency(repos, task_id, depends_on_task_id)
    if existing is not None:
        return existing

    edges = _dependency_edges(repos, workspace_id)
    edges.setdefault(task_id, set()).add(depends_on_task_id)
    cycle = dependency_cycle(edges)
    if cycle is not None:
        titles = _dependency_titles(repos, set(cycle))
        path = " → ".join(titles[item] for item in cycle)
        raise DomainError(f"任务依赖成环：{path}；成环的依赖不能当作可执行顺序")

    link = repos.tasks.add_dependency(workspace_id, task_id, depends_on_task_id)
    repos.uow.commit()
    return link


def dependency_check(
    repos: Repositories, workspace_id: int, *, semester_id: int | None = None
) -> dict:
    """检查依赖成环、范围外/缺失前置与已取消前置（A002）。

    成环基于整个工作空间，环在所选学期外也会报告；缺失、越界与取消只针对范围内任务。
    只读，不修改任务或依赖，也不自动解除成环边。
    """
    if semester_id is not None:
        require_semester_in_workspace(repos, workspace_id, semester_id)
    scoped = repos.tasks.list(workspace_id, semester_id=semester_id)
    scoped_ids = {task.id for task in scoped}
    all_tasks = repos.tasks.list(workspace_id)
    by_id = {task.id: task for task in all_tasks}
    edges = _dependency_edges(repos, workspace_id)

    cycles = []
    cycle_task_ids: set[int] = set()
    for component in cyclic_components(edges):
        cycle_task_ids.update(component)
        titles = [by_id[item].title if item in by_id else f"任务 {item}" for item in component]
        cycles.append(
            {
                "task_ids": component,
                "titles": titles,
                "message": "依赖成环：" + " ↔ ".join(titles),
            }
        )

    missing: list[dict] = []
    cancelled: list[dict] = []
    blocked: dict[int, dict] = {}

    def block(task: TaskData, reason: dict) -> None:
        entry = blocked.setdefault(
            task.id,
            {
                "task_id": task.id,
                "title": task.title,
                "reasons": [],
            },
        )
        entry["reasons"].append(reason)

    for task in scoped:
        if task.id in cycle_task_ids:
            block(
                task,
                {
                    "code": "dependency_cycle",
                    "message": "任务位于依赖环中，不能确定可执行顺序",
                },
            )

    for link in repos.tasks.list_workspace_dependencies(workspace_id):
        if link.task_id not in scoped_ids:
            continue
        task = by_id[link.task_id]
        prerequisite = by_id.get(link.depends_on_task_id)
        if prerequisite is None:
            reason = {
                "code": "prerequisite_missing",
                "depends_on_task_id": link.depends_on_task_id,
                "message": f"前置任务 {link.depends_on_task_id} 已不存在，无法满足依赖",
            }
            missing.append({**reason, "task_id": task.id, "title": task.title})
            block(task, reason)
        elif prerequisite.id not in scoped_ids:
            reason = {
                "code": "prerequisite_out_of_scope",
                "depends_on_task_id": prerequisite.id,
                "depends_on_title": prerequisite.title,
                "message": f"前置任务《{prerequisite.title}》不在当前学期范围，无法确认完成情况",
            }
            missing.append({**reason, "task_id": task.id, "title": task.title})
            block(task, reason)
        elif prerequisite.progress == TaskProgress.CANCELLED:
            reason = {
                "code": "prerequisite_cancelled",
                "depends_on_task_id": prerequisite.id,
                "depends_on_title": prerequisite.title,
                "message": f"前置任务《{prerequisite.title}》已取消，依赖无法满足",
            }
            cancelled.append({**reason, "task_id": task.id, "title": task.title})
            block(task, reason)

    return {
        "semester_id": semester_id,
        "valid": not cycles and not missing and not cancelled,
        "checked_tasks": len(scoped),
        "checked_edges": sum(
            1
            for link in repos.tasks.list_workspace_dependencies(workspace_id)
            if link.task_id in scoped_ids
        ),
        "cycles": cycles,
        "missing_prerequisites": missing,
        "cancelled_prerequisites": cancelled,
        "blocked_tasks": sorted(blocked.values(), key=lambda item: item["task_id"]),
    }


def remove_dependency(
    repos: Repositories, workspace_id: int, task_id: int, depends_on_task_id: int
) -> None:
    """解除一条必要依赖；关系不存在时按 404 处理，不静默成功。"""
    _require_task_in_workspace(repos, workspace_id, task_id)
    if not repos.tasks.remove_dependency(task_id, depends_on_task_id):
        raise NotFoundError("依赖关系不存在")
    repos.uow.commit()
