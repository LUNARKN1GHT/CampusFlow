"""RQ 默认保存完整异常堆栈；运维输出只保留作业编号和受控错误分类。

异常原文可能包含文件路径或外部响应等敏感信息，因此不写日志、不进失败登记；
排查请通过受登录与空间保护的作业状态与尝试历史接口进行。
"""

from rq import Worker


class SafeWorker(Worker):
    def handle_exception(self, job, *exc_info):
        """只记录作业编号与固定分类，不输出异常类型、消息或堆栈。"""
        self.log.error("job_id=%s error=processing_failed", job.id)

    def handle_job_failure(self, job, queue, started_job_registry=None, exc_string=""):
        """覆盖 RQ 默认保存的异常字符串，避免堆栈进入失败登记与运维输出。"""
        return super().handle_job_failure(
            job,
            queue,
            started_job_registry=started_job_registry,
            exc_string="processing_failed: 详情请查看授权作业状态接口",
        )
