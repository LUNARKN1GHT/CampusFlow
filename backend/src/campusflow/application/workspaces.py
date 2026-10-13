"""工作空间用例。

W005：当前会话的空间解析收敛到应用层——API 依赖只调用这里的用例，
不再直接操作 ORM 模型。默认空间的创建由仓储层配合数据库唯一约束保证并发安全。
"""

from campusflow.application.ports.repositories import Repositories, WorkspaceData

DEFAULT_WORKSPACE_NAME = "默认工作空间"
DEFAULT_TIMEZONE = "Asia/Shanghai"


def get_or_create_default_workspace(repos: Repositories) -> WorkspaceData:
    """返回当前默认工作空间；不存在时创建（并发安全由仓储唯一约束保证）。"""
    workspace = repos.workspaces.get_or_create_default(DEFAULT_WORKSPACE_NAME, DEFAULT_TIMEZONE)
    repos.uow.commit()
    return workspace
