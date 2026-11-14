"""粘贴文本的显式识别入口（BUG-554）。

保存与识别分开：粘贴文本导入只保存不可变原文与片段，不自动调用模型，
也不自动创建后台作业。只有用户显式点击“开始识别”时，才为当前版本：

1. 准备问答索引（创建或复用 embed 作业，交给现有 Worker 执行）；
2. 调用文字模型提取任务候选，整批保存为待核对事项。

重复调用不会重复建作业或重复生成候选：索引作业由版本唯一约束兜底，
提取状态写入版本元数据；材料行锁把同一份资料的并发点击串行化。
模型调用期间不持有任何数据库事务或行锁。
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from campusflow.application.candidates import save_candidates
from campusflow.application.dispatch import dispatch_pending_jobs
from campusflow.application.materials import get_material
from campusflow.application.ports.generation import TextGenerator
from campusflow.application.ports.queue import JobQueue
from campusflow.application.ports.repositories import (
    MaterialVersionData,
    ProcessingJobData,
    Repositories,
)
from campusflow.application.task_extraction import extract_task_drafts
from campusflow.domain.candidates import CandidateKind
from campusflow.domain.errors import DomainError, NotFoundError
from campusflow.domain.states import JobStage, JobStatus, MaterialSourceType, ReviewStatus

logger = logging.getLogger(__name__)

# 提取状态写在版本元数据里：与解析覆盖范围一样，只补充处理状态，不改写原文。
EXTRACTION_KEY = "candidate_extraction"
# 提取在进程内同步执行；超过这个时间仍为 running，视为上次进程中断，可重新开始。
EXTRACTION_STALE_AFTER = timedelta(minutes=10)

IndexState = Literal["not_started", "queued", "running", "done", "partial", "failed"]
ExtractionState = Literal["not_started", "running", "done", "empty", "failed"]


@dataclass(frozen=True)
class RecognitionIndexState:
    """当前版本的问答索引状态；只反映 embed 作业，不冒充事项提取。"""

    state: IndexState
    job_id: int | None
    searchable: bool
    reason: str | None
    updated_at: datetime | None


@dataclass(frozen=True)
class RecognitionExtractionState:
    """当前版本的任务候选提取状态；候选数量只统计任务类型。"""

    state: ExtractionState
    candidate_count: int
    pending_count: int
    reason: str | None
    extracted_at: datetime | None


@dataclass(frozen=True)
class RecognitionStatus:
    """资料识别的真实状态：原文保存完成不等于识别完成。"""

    material_id: int
    version_no: int | None
    chunk_count: int
    source_type: MaterialSourceType
    archived: bool
    supported: bool
    can_start: bool
    blocked_reason: str | None
    index: RecognitionIndexState
    extraction: RecognitionExtractionState


@dataclass(frozen=True)
class RecognitionStart:
    """一次显式启动的结果；started 为 false 表示没有重复执行。"""

    status: RecognitionStatus
    started: bool
    message: str


def _latest_version(repos: Repositories, material_id: int) -> MaterialVersionData | None:
    versions = repos.materials.list_versions(material_id)
    return versions[-1] if versions else None


def _version(repos: Repositories, material_id: int, version_no: int) -> MaterialVersionData | None:
    versions = repos.materials.list_versions(material_id)
    return next((item for item in versions if item.version_no == version_no), None)


def _embed_job(repos: Repositories, material_id: int, version_id: int) -> ProcessingJobData | None:
    """同一版本最多一个索引作业（Q004）；取最近的一条，不猜测其它阶段。"""
    jobs = [
        job
        for job in repos.jobs.list_by_material(material_id)
        if job.stage == JobStage.EMBED and job.version_id == version_id
    ]
    return jobs[-1] if jobs else None


def _index_state(job: ProcessingJobData | None) -> RecognitionIndexState:
    if job is None:
        return RecognitionIndexState(
            state="not_started",
            job_id=None,
            searchable=False,
            reason="原文已保存，尚未准备问答索引",
            updated_at=None,
        )
    mapping: dict[JobStatus, IndexState] = {
        JobStatus.PENDING: "queued",
        JobStatus.RUNNING: "running",
        JobStatus.DONE: "done",
        JobStatus.PARTIAL: "partial",
        JobStatus.FAILED: "failed",
    }
    status = JobStatus(job.status)
    state = mapping[status]
    reason = None
    if state == "queued":
        # 尚未成功投递与已投递等待 Worker 都算排队；不把“已排队”说成“已完成”。
        reason = "问答索引排队中，等待后台处理"
    elif state in ("partial", "failed"):
        reason = job.error_reason or "问答索引未完成，可重试"
    return RecognitionIndexState(
        state=state,
        job_id=job.id,
        searchable=status == JobStatus.DONE,
        reason=reason,
        updated_at=job.updated_at,
    )


def _marker(version: MaterialVersionData | None) -> dict[str, object]:
    if version is None:
        return {}
    raw = version.source_metadata.get(EXTRACTION_KEY)
    return raw if isinstance(raw, dict) else {}


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _task_candidates(repos: Repositories, workspace_id: int, version_id: int | None):
    if version_id is None:
        return []
    return [
        candidate
        for candidate in repos.candidates.list_by_version(workspace_id, version_id)
        if candidate.draft.kind == CandidateKind.TASK
    ]


def _is_stale_running(marker: dict[str, object], now: datetime) -> bool:
    started_at = _parse_time(marker.get("started_at"))
    return started_at is None or now - started_at > EXTRACTION_STALE_AFTER


def _extraction_state(
    repos: Repositories,
    workspace_id: int,
    version: MaterialVersionData | None,
    *,
    now: datetime,
) -> RecognitionExtractionState:
    """按提取记录与实际候选计算状态；无法确认时如实显示未开始。"""
    candidates = _task_candidates(repos, workspace_id, version.id if version else None)
    pending = sum(1 for item in candidates if item.review_status == ReviewStatus.PENDING)
    marker = _marker(version)
    status = marker.get("status")
    extracted_at = _parse_time(marker.get("extracted_at"))

    if status == "running":
        if _is_stale_running(marker, now):
            return RecognitionExtractionState(
                state="failed",
                candidate_count=len(candidates),
                pending_count=pending,
                reason="上次识别未完成，可重新开始",
                extracted_at=None,
            )
        return RecognitionExtractionState(
            state="running",
            candidate_count=len(candidates),
            pending_count=pending,
            reason="正在识别事项，请稍候",
            extracted_at=None,
        )
    if status == "done":
        return RecognitionExtractionState(
            state="done",
            candidate_count=len(candidates),
            pending_count=pending,
            reason=None,
            extracted_at=extracted_at,
        )
    if status == "empty":
        return RecognitionExtractionState(
            state="empty",
            candidate_count=len(candidates),
            pending_count=pending,
            reason="现有原文中未提取到需要学生完成的任务；这不等于没有作业要求",
            extracted_at=extracted_at,
        )
    if status == "failed":
        message = marker.get("message")
        return RecognitionExtractionState(
            state="failed",
            candidate_count=len(candidates),
            pending_count=pending,
            reason=(
                message
                if isinstance(message, str) and message.strip()
                else "上次识别未完成，可重新开始"
            ),
            extracted_at=None,
        )
    if candidates:
        # 通过其它入口生成的任务候选同样算“已提取”，不重复调用模型。
        return RecognitionExtractionState(
            state="done",
            candidate_count=len(candidates),
            pending_count=pending,
            reason=None,
            extracted_at=None,
        )
    return RecognitionExtractionState(
        state="not_started",
        candidate_count=0,
        pending_count=0,
        reason="原文已保存，尚未提取事项",
        extracted_at=None,
    )


def _blocked_reason(material, version: MaterialVersionData | None, chunk_count: int) -> str | None:
    if material.archived:
        return "资料已归档，恢复后才能开始识别"
    if material.source_type != MaterialSourceType.TEXT:
        return "当前只支持对粘贴文本开始识别；文件资料在解析完成后会自动准备问答索引"
    if version is None or chunk_count == 0:
        return "原文尚未切分，无法开始识别"
    return None


def _can_start(
    blocked_reason: str | None,
    index: RecognitionIndexState,
    extraction: RecognitionExtractionState,
) -> bool:
    if blocked_reason is not None:
        return False
    if extraction.state == "running":
        return False
    # 排队中的索引允许再次点击：队列投递失败时可以借此补投；已投递的作业不会重复入队。
    index_settled = index.state in ("running", "done")
    extraction_settled = extraction.state in ("done", "empty")
    return not (index_settled and extraction_settled)


def recognition_status(
    repos: Repositories, workspace_id: int, material_id: int
) -> RecognitionStatus:
    """读取资料识别状态；只读，不创建作业、不调用模型、不写库。"""
    material = get_material(repos, workspace_id, material_id)
    version = _latest_version(repos, material_id)
    chunk_count = len(repos.materials.list_chunks(version.id)) if version is not None else 0
    index = _index_state(
        _embed_job(repos, material_id, version.id) if version is not None else None
    )
    extraction = _extraction_state(repos, workspace_id, version, now=datetime.now(UTC))
    blocked = _blocked_reason(material, version, chunk_count)
    return RecognitionStatus(
        material_id=material_id,
        version_no=version.version_no if version is not None else None,
        chunk_count=chunk_count,
        source_type=material.source_type,
        archived=material.archived,
        supported=material.source_type == MaterialSourceType.TEXT,
        can_start=_can_start(blocked, index, extraction),
        blocked_reason=blocked,
        index=index,
        extraction=extraction,
    )


def _index_job_for_start(
    repos: Repositories, workspace_id: int, material_id: int, version_id: int
) -> tuple[ProcessingJobData, bool]:
    """准备索引作业；返回（作业，是否需要投递）。

    已排队、处理中或已完成的作业保持原样，避免重复索引；失败或部分成功
    的作业重设为待处理，保留已完成块。
    """
    existing = _embed_job(repos, material_id, version_id)
    if existing is None:
        job = repos.jobs.create_or_requeue(workspace_id, material_id, version_id, JobStage.EMBED)
        return job, job.enqueued_at is None
    if existing.status in (JobStatus.FAILED, JobStatus.PARTIAL):
        job = repos.jobs.create_or_requeue(workspace_id, material_id, version_id, JobStage.EMBED)
        return job, job.enqueued_at is None
    # 尚未成功投递的排队作业需要补投；已投递的不重复入队。
    return existing, existing.status == JobStatus.PENDING and existing.enqueued_at is None


def _should_skip_extraction(
    marker: dict[str, object], candidates, *, now: datetime
) -> tuple[bool, str | None]:
    """判断是否还有必要调用模型；返回（跳过, 说明）。

    已完成、空结果或已有任务候选都跳过；正在运行且未超时也跳过，
    避免并发请求重复调用模型。上次中断的超时运行允许重新开始。
    """
    status = marker.get("status")
    if status in ("done", "empty"):
        return True, "事项识别已完成，无需重复执行"
    if status == "running" and not _is_stale_running(marker, now):
        return True, "事项识别正在进行，请稍候刷新状态"
    if candidates:
        return True, "该版本已有任务候选，未重复调用模型"
    return False, None


def _write_marker(repos: Repositories, version_id: int, values: dict[str, object]) -> None:
    repos.materials.merge_version_metadata(version_id, {EXTRACTION_KEY: values})
    repos.uow.commit()


def _mark_failed_best_effort(repos: Repositories, version_id: int) -> None:
    """模型或保存失败后留下可重试标记；留痕失败不掩盖原始错误。"""
    try:
        repos.uow.rollback()
        _write_marker(
            repos,
            version_id,
            {
                "status": "failed",
                "kind": CandidateKind.TASK.value,
                "failed_at": datetime.now(UTC).isoformat(),
                "message": "上次识别未完成，可重新开始",
            },
        )
    except Exception:  # noqa: BLE001 - 留痕失败不应覆盖调用方的原始异常
        repos.uow.rollback()
        logger.warning("识别失败留痕写入失败：version_id=%s", version_id)


def start_recognition(
    repos: Repositories,
    queue: JobQueue | None,
    workspace_id: int,
    material_id: int,
    *,
    generator: TextGenerator,
    operator: str,
) -> RecognitionStart:
    """显式启动一次识别：准备问答索引并提取任务候选。

    保存与识别分开，绝不自动执行；重复点击不会重复建作业或候选。
    模型调用在提交提取标记之后进行，期间不持有事务或行锁。
    """
    # 先锁资料行：同一份资料的并发点击在这里串行化，后到者会看到 running 标记。
    if not repos.materials.lock(workspace_id, material_id):
        raise NotFoundError("资料不存在")
    material = repos.materials.get(material_id)
    if material is None:
        raise NotFoundError("资料不存在")
    if material.archived:
        raise DomainError("资料已归档，请恢复后再开始识别")
    if material.source_type != MaterialSourceType.TEXT:
        raise DomainError("当前只支持对粘贴文本开始识别；文件资料在解析完成后会自动准备问答索引")
    version = _latest_version(repos, material_id)
    if version is None:
        raise DomainError("原文尚未切分，无法开始识别")
    if not repos.materials.list_chunks(version.id):
        raise DomainError("原文尚未切分，无法开始识别")

    index_job, needs_dispatch = _index_job_for_start(repos, workspace_id, material_id, version.id)
    # 索引作业与提取标记分开提交：先让索引进入队列，后面的模型调用失败也不回滚它。
    repos.uow.commit()

    if needs_dispatch:
        dispatch_pending_jobs(repos, queue, job_ids=[index_job.id])

    # 重新加锁读取，确保看到其它并在本轮之前完成的提取结果。
    if not repos.materials.lock(workspace_id, material_id):
        raise NotFoundError("资料不存在")
    version = _version(repos, material_id, version.version_no)
    if version is None:
        raise NotFoundError("来源版本不存在")
    now = datetime.now(UTC)
    marker = _marker(version)
    candidates = _task_candidates(repos, workspace_id, version.id)
    skip, skip_message = _should_skip_extraction(marker, candidates, now=now)
    if skip:
        repos.uow.commit()
        status = recognition_status(repos, workspace_id, material_id)
        return RecognitionStart(
            status=status, started=False, message=skip_message or "无需重复执行"
        )

    _write_marker(
        repos,
        version.id,
        {
            "status": "running",
            "kind": CandidateKind.TASK.value,
            "started_at": now.isoformat(),
            "operator": operator,
        },
    )

    try:
        drafts = extract_task_drafts(
            repos, workspace_id, material_id, version.version_no, generator
        )
        saved = save_candidates(repos, workspace_id, material_id, version.version_no, drafts)
    except Exception:
        _mark_failed_best_effort(repos, version.id)
        raise

    _write_marker(
        repos,
        version.id,
        {
            "status": "done" if saved else "empty",
            "kind": CandidateKind.TASK.value,
            "candidate_count": len(saved),
            "extracted_at": datetime.now(UTC).isoformat(),
            "operator": operator,
        },
    )
    status = recognition_status(repos, workspace_id, material_id)
    message = (
        f"已保存 {len(saved)} 条待核对事项，可到待核对页面逐条核对"
        if saved
        else "现有原文中未提取到需要学生完成的任务；这不等于没有作业要求"
    )
    return RecognitionStart(status=status, started=True, message=message)
