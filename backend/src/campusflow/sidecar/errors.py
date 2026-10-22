"""sidecar 的受控失败类型：只携带可展示给用户的中文说明，不附带连接串。"""


class SidecarError(RuntimeError):
    """运行失败（退出码 1）：例如找不到 pg_dump、目标库非空、归档损坏。"""


class UsageError(ValueError):
    """参数使用错误（退出码 2）：例如端口越界、输出路径缺失。"""
