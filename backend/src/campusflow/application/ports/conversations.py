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
    def create(self, workspace_id: int, scope: RetrievalScope, mode: str) -> QaConversationData: ...

    def get(self, workspace_id: int, conversation_id: int) -> QaConversationData | None: ...

    def update_scope(
        self, workspace_id: int, conversation_id: int, scope: RetrievalScope
    ) -> QaConversationData | None: ...

    def list(self, workspace_id: int, *, limit: int = 50) -> list[QaConversationData]: ...

    def add_turn(self, workspace_id: int, conversation_id: int, turn: NewQaTurn) -> QaTurnData: ...

    def list_turns(self, workspace_id: int, conversation_id: int) -> list[QaTurnData]: ...
