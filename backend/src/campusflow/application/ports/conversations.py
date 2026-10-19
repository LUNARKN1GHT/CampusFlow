"""Q014/Q017：会话存储端口，不暴露 ORM 或数据库事务。"""

from __future__ import annotations

from typing import Protocol

from campusflow.domain.conversation import (
    NewQaTurn,
    QaConversationData,
    QaTurnData,
)
from campusflow.domain.retrieval import RetrievalScope


class ConversationStore(Protocol):
    """会话与轮次存储；所有读写都限定工作空间，跨空间按不存在处理。

    轮次保存当时的范围与引用快照；切换会话范围只影响后续追问，不重写旧轮次。
    """

    def create(self, workspace_id: int, scope: RetrievalScope, mode: str) -> QaConversationData:
        """创建会话并保存初始范围；范围合法性由应用层先校验。"""
        ...

    def get(self, workspace_id: int, conversation_id: int) -> QaConversationData | None:
        """限定空间读取会话；跨空间与不存在都返回 None。"""
        ...

    def update_scope(
        self, workspace_id: int, conversation_id: int, scope: RetrievalScope
    ) -> QaConversationData | None:
        """显式切换当前范围；旧轮次保留各自快照，不随后续切换改变。"""
        ...

    def list(self, workspace_id: int, *, limit: int = 50) -> list[QaConversationData]: ...

    def add_turn(self, workspace_id: int, conversation_id: int, turn: NewQaTurn) -> QaTurnData:
        """追加一轮问答；会话不存在或跨空间时抛 NotFoundError。"""
        ...

    def list_turns(self, workspace_id: int, conversation_id: int) -> list[QaTurnData]:
        """按发生顺序返回轮次；会话不存在或跨空间时抛 NotFoundError。"""
        ...
