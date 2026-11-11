"""资料状态视图（#555）：批量派生原文读取与问答索引状态。

列表与详情共用同一派生入口。一次查询取每份资料当前版本的解析作业与索引
作业，避免按资料逐个查询；旧版本的失败记录不会覆盖当前版本的成功结果。
"""

from collections.abc import Sequence
from dataclasses import dataclass

from campusflow.application.ports.repositories import (
    MaterialData,
    ProcessingJobData,
    Repositories,
)
from campusflow.domain.material_status import (
    IndexAvailability,
    IndexJobSignal,
    TextReadState,
    index_availability,
    text_read_state,
)
from campusflow.domain.states import JobStage


@dataclass(frozen=True)
class MaterialProcessingView:
    """资料列表/详情展示用的两条状态轴。"""

    text_state: TextReadState
    index: IndexAvailability


def build_processing_views(
    repos: Repositories, materials: Sequence[MaterialData]
) -> dict[int, MaterialProcessingView]:
    """批量构造资料处理视图；返回 资料 id → 视图。

    只发一条作业查询（每份资料每阶段取当前版本的最新作业）。没有作业的资料
    也返回视图：原文状态回退到资料状态，问答索引明确为“未开始”，不伪造可用。
    """
    if not materials:
        return {}
    jobs = repos.jobs.latest_stage_jobs_by_materials(
        [material.id for material in materials],
        stages=(JobStage.PARSE, JobStage.EMBED),
    )
    views: dict[int, MaterialProcessingView] = {}
    for material in materials:
        parse_job = jobs.get((material.id, JobStage.PARSE))
        embed_job = jobs.get((material.id, JobStage.EMBED))
        text_state = text_read_state(
            material_status=material.status,
            parse_status=parse_job.status if parse_job is not None else None,
        )
        views[material.id] = MaterialProcessingView(
            text_state=text_state,
            index=index_availability(
                source_type=material.source_type,
                archived=material.archived,
                text_state=text_state,
                job=_index_signal(embed_job),
            ),
        )
    return views


def _index_signal(job: ProcessingJobData | None) -> IndexJobSignal | None:
    if job is None:
        return None
    return IndexJobSignal(
        job_id=job.id,
        status=job.status,
        error_reason=job.error_reason,
        scope=job.scope,
        retryable=job.retryable,
        manifest_id=job.manifest_id,
        delivered=job.enqueued_at is not None,
    )
