"""领域错误。API 层统一转换为 HTTP 响应。

默认处理器把 DomainError 映射为 400，NotFoundError／ConflictError 另有
404／409；问答入口的模型失败需先经 classify_model_failure 才会得到 502／503。
"""


class DomainError(Exception):
    """业务规则错误（400）。"""


class NotFoundError(DomainError):
    """对象不存在（404）；跨工作空间访问也用此错误，避免泄露对象存在性。"""


class ConflictError(DomainError):
    """并发或版本冲突（409）：过期草案、重复接受或输入已变化。"""


class TransientError(DomainError):
    """临时故障（限流、超时、上游 5xx、连接中断）：处理作业可重试（J005）。

    继承 DomainError 只为统一捕获；问答入口会先分类并返回 503，
    而不是落到默认的 400。
    """


class PermanentError(DomainError):
    """永久失败（格式不支持、来源已删除）：重试不会成功（J005）。"""


class ModelOutputError(PermanentError):
    """模型返回了响应，但内容不可用（结构错误、超限、维度不一致）。

    与「请求被拒绝」区分：这类失败重试通常无效，但也不是服务不可用。
    它是 PermanentError 的子类，分类时必须先于 PermanentError 判断。
    """
