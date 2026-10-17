"""RQ 默认保存完整异常堆栈；运维输出只保留作业编号和受控错误分类。"""

from rq import Worker


class SafeWorker(Worker):
    def handle_exception(self, job, *exc_info):
        self.log.error("job_id=%s error=processing_failed", job.id)

    def handle_job_failure(self, job, queue, started_job_registry=None, exc_string=""):
        return super().handle_job_failure(
            job,
            queue,
            started_job_registry=started_job_registry,
            exc_string="processing_failed: 详情请查看授权作业状态接口",
        )
