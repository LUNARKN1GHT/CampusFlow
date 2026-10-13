"""视觉模型识别错误（D014）。

识别失败必须可报告——作业层据此把任务标记为失败/可重试，
绝不允许在失败时生成"确定事实"。
"""

from campusflow.domain.errors import TransientError


class VisionRecognitionError(TransientError, RuntimeError):
    """视觉识别调用失败（网络、限流、超时或响应无法解析）。

    归类为临时故障（J005）：模型服务抖动能重试，不属于永久失败。
    """
