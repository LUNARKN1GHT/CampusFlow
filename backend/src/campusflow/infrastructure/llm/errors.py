"""视觉模型识别错误（D014、Q009）。

识别失败必须可报告——作业层据此把任务标记为失败/可重试，
绝不允许在失败时生成"确定事实"。

Q009 起按可重试性分层：
- 服务抖动（网络、限流、5xx、超时）是临时故障，可重试；
- 请求被拒绝（其他 4xx）是永久失败，重试不会成功；
- 模型返回内容无法解析也是永久失败，但与「请求被拒绝」区分开。
"""

from campusflow.domain.errors import ModelOutputError, PermanentError, TransientError


class VisionRecognitionFailure(RuntimeError):
    """视觉识别失败的统一基类，供 PDF 单页降级捕获；具体子类仍按可重试性分类。"""


class VisionRecognitionError(VisionRecognitionFailure, TransientError):
    """视觉识别临时故障（网络、限流、超时或上游 5xx）。"""


class VisionRequestRejected(VisionRecognitionFailure, PermanentError):
    """视觉识别请求被拒绝（账号、配置或不可重试的 4xx）。"""


class VisionInvalidOutput(VisionRecognitionFailure, ModelOutputError):
    """视觉模型返回了响应，但内容无法解析或超过读取上限。"""
