"""资料的两条独立状态轴（#555）：原文读取与问答索引。

资料 `status` 只代表原文解析结果；问答索引的结果单独记录在索引作业里。
列表与详情必须分别展示两条轴，不能把“原文读取完成”推断成“已经可以问答”。
本模块只做纯规则映射，不查库、不调用模型，输入是作业的已保存事实。
"""

from dataclasses import dataclass
from enum import StrEnum

from campusflow.domain.indexing import (
    INDEX_ALL_BLOCKS_FAILED_REASON,
    INDEX_CONFIGURATION_MISSING_REASON,
    INDEX_PARTIAL_BLOCKS_FAILED_REASON,
    INDEX_PROFILE_MISMATCH_REASON,
    INDEX_SOURCE_CHANGED_REASON,
    INDEX_SOURCE_UNAVAILABLE_REASON,
)
from campusflow.domain.jobs import TIMEOUT_REASON_PREFIX, can_retry
from campusflow.domain.states import JobStatus, MaterialSourceType, MaterialStatus


class TextReadState(StrEnum):
    """原文读取状态：只描述“文字有没有读出来”，与能否问答无关。"""

    QUEUED = "queued"  # 待读取（作业未投递或排队中）
    READING = "reading"  # 读取中
    READY = "ready"  # 已读取
    PARTIAL = "partial"  # 部分读取，未覆盖范围必须另行展示
    FAILED = "failed"  # 读取失败


class IndexState(StrEnum):
    """问答索引状态：未开始、准备中、可用、部分可用、失败与无法核对。"""

    NOT_STARTED = "not_started"
    PREPARING = "preparing"
    READY = "ready"
    PARTIAL = "partial"
    FAILED = "failed"
    UNKNOWN = "unknown"  # 记录不完整，不能确认可用


@dataclass(frozen=True)
class IndexJobSignal:
    """派生索引状态所需的最小作业事实；不含正文、向量或模型输出。"""

    job_id: int
    status: str
    error_reason: str | None
    scope: str | None
    retryable: bool | None
    manifest_id: str | None
    delivered: bool


@dataclass(frozen=True)
class IndexAvailability:
    """问答索引的可读状态；message 是可展示说明，reason_code 是稳定协议值。"""

    state: IndexState
    reason_code: str
    message: str
    detail: str | None = None
    error: str | None = None
    job_id: int | None = None
    retryable: bool = False


_TEXT_JOB_STATES = {
    JobStatus.PENDING: TextReadState.QUEUED,
    JobStatus.RUNNING: TextReadState.READING,
    JobStatus.DONE: TextReadState.READY,
    JobStatus.PARTIAL: TextReadState.PARTIAL,
    JobStatus.FAILED: TextReadState.FAILED,
}

_TEXT_MATERIAL_STATES = {
    MaterialStatus.PENDING: TextReadState.QUEUED,
    MaterialStatus.PROCESSING: TextReadState.READING,
    MaterialStatus.DONE: TextReadState.READY,
    MaterialStatus.PARTIAL: TextReadState.PARTIAL,
    MaterialStatus.FAILED: TextReadState.FAILED,
}

# 作业 error_reason 是固定文案；映射到稳定原因代码与下一步说明。
_INDEX_FAILURE_REASONS = {
    INDEX_SOURCE_CHANGED_REASON: (
        "index_source_changed",
        "问答索引来源在处理过程中发生变化，当前索引未完成；可重试索引重新生成清单。",
    ),
    INDEX_SOURCE_UNAVAILABLE_REASON: (
        "index_source_unavailable",
        "索引来源已不可用或已归档，问答索引无法继续；原文读取结果仍保留。",
    ),
    INDEX_PROFILE_MISMATCH_REASON: (
        "index_model_profile_mismatch",
        "向量模型配置缺失或与已登记配置不一致，问答索引未建立；"
        "原文读取结果已保留，请先检查向量模型配置。",
    ),
    INDEX_CONFIGURATION_MISSING_REASON: (
        "index_configuration_missing",
        "缺少向量模型配置，问答索引未完成；原文已读取并保留，"
        "请在设置 → AI 服务配置 ZHIPU 密钥并重启应用后重试索引。",
    ),
    INDEX_ALL_BLOCKS_FAILED_REASON: (
        "index_failed_all_blocks",
        "问答索引全部失败，这份资料目前不能用于问答；"
        "原文已读取并保留，可查看原文，失败详情见处理记录。",
    ),
    INDEX_PARTIAL_BLOCKS_FAILED_REASON: (
        "index_partial_blocks_failed",
        "问答索引未完整完成，只有已成功索引的部分可用于问答；"
        "未覆盖或未索引内容不会被当作没有要求。",
    ),
}


def text_read_state(*, material_status: str, parse_status: str | None = None) -> TextReadState:
    """原文读取状态。

    有解析作业时以作业为准，才能区分“待投递”和“读取中”；没有作业的粘贴
    文本（同步切分）以资料状态为准。资料状态不参与问答索引判断。
    """
    if parse_status is not None:
        try:
            return _TEXT_JOB_STATES[JobStatus(parse_status)]
        except ValueError:
            pass
    try:
        return _TEXT_MATERIAL_STATES[MaterialStatus(material_status)]
    except ValueError:
        return TextReadState.QUEUED


def index_availability(
    *,
    source_type: str,
    archived: bool,
    text_state: TextReadState,
    job: IndexJobSignal | None,
) -> IndexAvailability:
    """按当前版本索引作业派生问答索引状态。

    没有作业表示未开始而不是可用；原文失败或尚未读完时，说明不能暗示原文
    已保存可索引；待投递与处理中同属准备中但原因代码不同；完成还必须带清单
    才能确认；失败与部分成功保留作业的覆盖说明和脱敏原因。
    """
    availability = _index_availability(source_type, text_state, job)
    if not archived:
        return availability
    return IndexAvailability(
        state=availability.state,
        reason_code=availability.reason_code,
        message=availability.message + " 资料已归档：当前不参与问答，恢复后按索引记录重新生效。",
        detail=availability.detail,
        error=availability.error,
        job_id=availability.job_id,
        retryable=availability.retryable,
    )


def _index_availability(
    source_type: str, text_state: TextReadState, job: IndexJobSignal | None
) -> IndexAvailability:
    if job is None:
        message = "问答索引未开始：这份资料还不能用于问答。"
        if text_state is TextReadState.FAILED:
            message += "原文未读取成功，无法建立问答索引；可先重试原文处理。"
        elif text_state is TextReadState.PARTIAL:
            message += "原文仅部分读取，建立索引后也只能覆盖已读取部分。"
        elif text_state in (TextReadState.QUEUED, TextReadState.READING):
            message += "原文读取尚未完成，读取完成后才会建立索引。"
        elif _is_pasted_text(source_type):
            message += "粘贴文本目前不会自动建立问答索引。"
        else:
            message += "原文读取结果已保存，索引建立后才能用于问答。"
        return IndexAvailability(IndexState.NOT_STARTED, "index_not_started", message)

    status = _job_status(job.status)
    if status is JobStatus.PENDING:
        if not job.delivered:
            return _state(
                job,
                IndexState.PREPARING,
                "index_waiting_delivery",
                "问答索引等待处理：作业尚未投递到处理队列，完成后才能用于问答。",
            )
        return _state(
            job,
            IndexState.PREPARING,
            "index_queued",
            "问答索引已排队，尚未开始处理；完成后才能用于问答。",
        )
    if status is JobStatus.RUNNING:
        return _state(
            job,
            IndexState.PREPARING,
            "index_running",
            "问答索引进行中；完成前这份资料不能用于问答。",
        )
    if status is JobStatus.DONE:
        if job.manifest_id is None:
            return _state(
                job,
                IndexState.UNKNOWN,
                "index_unverified",
                "索引作业已结束，但缺少可核对的索引清单；不能确认这份资料可以问答。",
            )
        return _state(
            job, IndexState.READY, "index_ready", "问答索引可用：可以基于这份资料进行问答。"
        )
    if status is JobStatus.PARTIAL:
        code, message = _failure_reason(job, partial=True)
        return _state(job, IndexState.PARTIAL, code, message)
    if status is JobStatus.FAILED:
        code, message = _failure_reason(job, partial=False)
        return _state(job, IndexState.FAILED, code, message)
    return _state(
        job,
        IndexState.UNKNOWN,
        "index_status_unknown",
        "索引作业状态无法识别，不能确认这份资料可以问答；请查看处理作业详情。",
    )


def _state(
    job: IndexJobSignal, state: IndexState, reason_code: str, message: str
) -> IndexAvailability:
    detail = (job.scope or "").strip() or None
    error = (job.error_reason or "").strip() or None
    return IndexAvailability(
        state=state,
        reason_code=reason_code,
        message=message,
        detail=detail,
        error=error,
        job_id=job.job_id,
        retryable=can_retry(job.status, job.retryable),
    )


def _failure_reason(job: IndexJobSignal, *, partial: bool) -> tuple[str, str]:
    reason = (job.error_reason or "").strip()
    if reason.startswith(TIMEOUT_REASON_PREFIX):
        return (
            "index_timeout",
            "问答索引处理超时，已标记为可重试失败；原文读取结果仍保留。",
        )
    if reason in _INDEX_FAILURE_REASONS:
        return _INDEX_FAILURE_REASONS[reason]
    if partial:
        return (
            "index_partial",
            "问答索引部分完成，只有已成功部分可用于问答；请查看覆盖说明。",
        )
    return (
        "index_failed",
        "问答索引失败，这份资料目前不能用于问答；原文已读取并保留，失败详情见处理记录。",
    )


def _job_status(raw: str) -> JobStatus | None:
    try:
        return JobStatus(raw)
    except ValueError:
        return None


def _is_pasted_text(source_type: str) -> bool:
    try:
        return MaterialSourceType(source_type) is MaterialSourceType.TEXT
    except ValueError:
        return False
