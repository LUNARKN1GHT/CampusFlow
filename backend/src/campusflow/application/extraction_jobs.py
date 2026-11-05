"""把既有任务、活动和规则提取串成可恢复的后台作业；结果只进入待核对。"""

import json
from dataclasses import asdict

from campusflow.application.candidates import save_candidates
from campusflow.application.event_extraction import extract_event_drafts
from campusflow.application.ports.generation import TextGenerator
from campusflow.application.ports.repositories import Repositories
from campusflow.application.processing import blocked_write_reason
from campusflow.application.rule_extraction import extract_rule_drafts
from campusflow.application.task_extraction import extract_task_drafts
from campusflow.domain.errors import DomainError, ModelOutputError, PermanentError, TransientError
from campusflow.domain.states import JobStage, JobStatus


def _snapshot(repos: Repositories, job):
    if blocked_write_reason(repos, job):
        raise PermanentError("来源已删除或已有新版本")
    material = repos.materials.get(job.material_id)
    if material is None or material.archived:
        raise PermanentError("来源已删除或归档")
    version = next(
        v for v in repos.materials.list_versions(job.material_id) if v.id == job.version_id
    )
    chunks = repos.materials.list_chunks(job.version_id)
    if not chunks:
        raise PermanentError("来源没有可提取的原文")
    snapshot = json.dumps(
        {
            "scope": [material.semester_id, material.course_id, material.class_name],
            "published_at": material.published_at,
            "metadata": version.source_metadata,
            "chunks": [asdict(chunk) for chunk in chunks],
        },
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return version, snapshot


def extract_version(repos: Repositories, generator: TextGenerator, job_id: int) -> None:
    if not repos.jobs.claim(job_id):
        return
    repos.uow.commit()
    job = repos.jobs.get(job_id)
    if job is None:
        return
    try:
        if job.stage != JobStage.EXTRACT:
            raise DomainError("阶段不匹配")
        version, snapshot = _snapshot(repos, job)
        repos.uow.commit()
        drafts = []
        for extract in (extract_task_drafts, extract_event_drafts, extract_rule_drafts):
            drafts.extend(
                extract(repos, job.workspace_id, job.material_id, version.version_no, generator)
            )
        # 结束最后一轮只读事务，再锁定本次领取及来源，避免旧 Worker 晚到覆盖。
        repos.uow.rollback()
        if not repos.jobs.lock_running(job_id, job.started_at):
            repos.uow.rollback()
            return
        _, current = _snapshot(repos, job)
        if current != snapshot:
            raise PermanentError("来源或适用范围在提取期间改变")
        saved = save_candidates(
            repos, job.workspace_id, job.material_id, version.version_no, drafts, commit=False
        )
        repos.jobs.record_attempt(
            job_id,
            status=JobStatus.DONE,
            scope=f"整理出 {len(saved)} 条候选事项，等待人工核对",
            error_reason=None,
            retryable=False,
        )
        repos.uow.commit()
    except Exception as error:
        repos.uow.rollback()
        if not repos.jobs.lock_running(job_id, job.started_at):
            repos.uow.rollback()
            return
        if isinstance(error, TransientError):
            reason = "事项提取服务暂时不可用，请稍后手动重试；原文已保留"
        elif isinstance(error, ModelOutputError):
            reason = "事项提取结果未通过校验，请检查模型配置后手动重试；原文已保留"
        elif isinstance(error, PermanentError) and str(error).startswith("文字模型配置不可用"):
            reason = "文字模型配置不可用，请到 AI 服务设置保存有效密钥与型号后手动重试"
        elif isinstance(error, PermanentError) and "文字模型请求被拒绝" in str(error):
            reason = "文字模型配置不可用，请检查账号权限、额度与型号后手动重试"
        else:
            reason = "事项提取未完成，来源不可用、已变化或内容不符合要求；请核对资料"
        repos.jobs.record_attempt(
            job_id,
            status=JobStatus.FAILED,
            scope=None,
            error_reason=reason,
            retryable=isinstance(error, TransientError),
        )
        repos.uow.commit()
