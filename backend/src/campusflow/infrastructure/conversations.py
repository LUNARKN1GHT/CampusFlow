"""Q014/Q017：会话与轮次的 PostgreSQL 存储。"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from campusflow.domain.conversation import (
    NewQaTurn,
    QaConversationData,
    QaTurnData,
    turn_from_columns,
)
from campusflow.domain.errors import NotFoundError
from campusflow.domain.retrieval import RetrievalScope
from campusflow.infrastructure.db.models import QaConversation, QaTurn


def _conversation(row: QaConversation) -> QaConversationData:
    return QaConversationData(
        id=row.id,
        workspace_id=row.workspace_id,
        scope=RetrievalScope(row.semester_id, row.course_id, row.class_name),
        mode=row.mode,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class SqlConversations:
    def __init__(self, session: Session):
        self.session = session

    def create(self, workspace_id: int, scope: RetrievalScope, mode: str) -> QaConversationData:
        row = QaConversation(
            workspace_id=workspace_id,
            semester_id=scope.semester_id,
            course_id=scope.course_id,
            class_name=scope.class_name,
            mode=mode,
        )
        self.session.add(row)
        self.session.flush()
        return _conversation(row)

    def get(self, workspace_id: int, conversation_id: int) -> QaConversationData | None:
        row = self.session.scalar(
            select(QaConversation).where(
                QaConversation.id == conversation_id,
                QaConversation.workspace_id == workspace_id,
            )
        )
        return None if row is None else _conversation(row)

    def update_scope(
        self, workspace_id: int, conversation_id: int, scope: RetrievalScope
    ) -> QaConversationData | None:
        row = self.session.scalar(
            select(QaConversation)
            .where(
                QaConversation.id == conversation_id,
                QaConversation.workspace_id == workspace_id,
            )
            .with_for_update()
        )
        if row is None:
            return None
        row.semester_id = scope.semester_id
        row.course_id = scope.course_id
        row.class_name = scope.class_name
        self.session.flush()
        return _conversation(row)

    def list(self, workspace_id: int, *, limit: int = 50) -> list[QaConversationData]:
        rows = self.session.scalars(
            select(QaConversation)
            .where(QaConversation.workspace_id == workspace_id)
            .order_by(QaConversation.updated_at.desc(), QaConversation.id.desc())
            .limit(limit)
        )
        return [_conversation(row) for row in rows]

    def add_turn(self, workspace_id: int, conversation_id: int, turn: NewQaTurn) -> QaTurnData:
        if self.get(workspace_id, conversation_id) is None:
            raise NotFoundError("问答会话不存在")
        columns = turn.to_columns()
        row = QaTurn(conversation_id=conversation_id, **columns)
        self.session.add(row)
        self.session.flush()
        conversation = self.session.get(QaConversation, conversation_id)
        conversation.updated_at = row.created_at
        return turn_from_columns(row)

    def list_turns(self, workspace_id: int, conversation_id: int) -> list[QaTurnData]:
        if self.get(workspace_id, conversation_id) is None:
            raise NotFoundError("问答会话不存在")
        rows = self.session.scalars(
            select(QaTurn).where(QaTurn.conversation_id == conversation_id).order_by(QaTurn.id)
        )
        return [turn_from_columns(row) for row in rows]
