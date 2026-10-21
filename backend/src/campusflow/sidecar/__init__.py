"""桌面 sidecar 命令入口（P006）：首启、迁移、备份、恢复、迁移包、升级、诊断与服务启动。

供 Electron 主进程与手工运维共用；命令只做进程装配与本地文件操作，
不承载业务规则，也不改写资料、任务或计划内容。
"""

from campusflow.sidecar.errors import SidecarError, UsageError

__all__ = ["SidecarError", "UsageError"]
