"""M9 外部日历持久化（SQLAlchemy）：连接、OAuth state、草案、执行记录与事件映射。

令牌字段只保存 Fernet 密文；本层只 flush 不提交，事务边界由应用用例控制。
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from campusflow.application.ports.calendar import (
    CalendarConnectionData,
    CalendarCredentialsData,
    CalendarDraftData,
    CalendarDraftItemData,
    CalendarEventLinkData,
    CalendarOAuthStateData,
    CalendarOperationData,
    CalendarStore,
)
from campusflow.domain.calendar import (
    CalendarSourceType,
    ConnectionStatus,
    DraftStatus,
    OperationStatus,
    OperationType,
)
from campusflow.infrastructure.db.models import (
    CalendarConnection,
    CalendarDraft,
    CalendarDraftItem,
    CalendarEventLink,
    CalendarOAuthState,
    CalendarOperation,
)


def _connection_to_data(row: CalendarConnection) -> CalendarConnectionData:
    return CalendarConnectionData(
        id=row.id,
        workspace_id=row.workspace_id,
        provider=row.provider,
        account_email=row.account_email,
        calendar_id=row.calendar_id,
        status=ConnectionStatus(row.status),
        scopes=row.scopes,
        token_expires_at=row.token_expires_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
        revoked_at=row.revoked_at,
    )


def _oauth_state_to_data(row: CalendarOAuthState) -> CalendarOAuthStateData:
    return CalendarOAuthStateData(
        id=row.id,
        workspace_id=row.workspace_id,
        provider=row.provider,
        state_hash=row.state_hash,
        redirect_uri=row.redirect_uri,
        expires_at=row.expires_at,
        consumed_at=row.consumed_at,
        created_at=row.created_at,
    )


def _item_to_data(row: CalendarDraftItem) -> CalendarDraftItemData:
    return CalendarDraftItemData(
        id=row.id,
        draft_id=row.draft_id,
        operation_type=OperationType(row.operation_type),
        source_type=CalendarSourceType(row.source_type),
        source_id=row.source_id,
        title=row.title,
        starts_at=row.starts_at,
        ends_at=row.ends_at,
        timezone=row.timezone,
        date_only=row.date_only,
        location=row.location,
        target_calendar_id=row.target_calendar_id,
        external_event_id=row.external_event_id,
        external_etag=row.external_etag,
        item_fingerprint=row.item_fingerprint,
        content_fingerprint=row.content_fingerprint,
        position=row.position,
    )


def _draft_to_data(row: CalendarDraft, items: list[CalendarDraftItemData]) -> CalendarDraftData:
    return CalendarDraftData(
        id=row.id,
        workspace_id=row.workspace_id,
        connection_id=row.connection_id,
        status=DraftStatus(row.status),
        version=row.version,
        content_fingerprint=row.content_fingerprint,
        confirmed_fingerprint=row.confirmed_fingerprint,
        confirmed_at=row.confirmed_at,
        expires_at=row.expires_at,
        excluded=list(row.excluded or []),
        warnings=list(row.warnings or []),
        note=row.note,
        created_at=row.created_at,
        updated_at=row.updated_at,
        items=items,
        confirmed_item_ids=list(row.confirmed_item_ids or []),
    )


def _operation_to_data(row: CalendarOperation) -> CalendarOperationData:
    return CalendarOperationData(
        id=row.id,
        workspace_id=row.workspace_id,
        draft_id=row.draft_id,
        item_id=row.item_id,
        operation_type=OperationType(row.operation_type),
        status=OperationStatus(row.status),
        idempotency_key=row.idempotency_key,
        external_event_id=row.external_event_id,
        external_etag=row.external_etag,
        error_category=row.error_category,
        error_message=row.error_message,
        attempt=row.attempt,
        result_payload=dict(row.result_payload or {}),
        started_at=row.started_at,
        finished_at=row.finished_at,
        created_at=row.created_at,
    )


def _link_to_data(row: CalendarEventLink) -> CalendarEventLinkData:
    return CalendarEventLinkData(
        id=row.id,
        workspace_id=row.workspace_id,
        connection_id=row.connection_id,
        source_type=CalendarSourceType(row.source_type),
        source_id=row.source_id,
        external_event_id=row.external_event_id,
        external_etag=row.external_etag,
        content_fingerprint=row.content_fingerprint,
        source_snapshot=dict(row.source_snapshot or {}),
        state=row.state,
        last_synced_at=row.last_synced_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class SqlCalendarStore(CalendarStore):
    """CalendarStore 的数据库实现；写入后由调用方决定何时提交或回滚。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def commit(self) -> None:
        self._session.commit()

    def flush(self) -> None:
        self._session.flush()

    def find_connection(
        self, workspace_id: int, provider: str, account_email: str
    ) -> CalendarConnectionData | None:
        """按账户身份查找连接；同一工作空间与账户只有一条连接（唯一约束）。"""
        row = self._session.scalar(
            select(CalendarConnection).where(
                CalendarConnection.workspace_id == workspace_id,
                CalendarConnection.provider == provider,
                CalendarConnection.account_email == account_email,
            )
        )
        return _connection_to_data(row) if row is not None else None

    def create_connection(
        self,
        workspace_id: int,
        *,
        provider: str,
        account_email: str,
        calendar_id: str,
        scopes: str,
        access_token_ciphertext: str,
        refresh_token_ciphertext: str | None,
        token_expires_at: datetime,
    ) -> CalendarConnectionData:
        """新建 active 连接；调用方必须传入已加密的令牌，本层不接触明文。"""
        row = CalendarConnection(
            workspace_id=workspace_id,
            provider=provider,
            account_email=account_email,
            calendar_id=calendar_id,
            status=ConnectionStatus.ACTIVE.value,
            scopes=scopes,
            access_token_ciphertext=access_token_ciphertext,
            refresh_token_ciphertext=refresh_token_ciphertext,
            token_expires_at=token_expires_at,
        )
        self._session.add(row)
        self._session.flush()
        return _connection_to_data(row)

    def update_connection_tokens(
        self,
        connection_id: int,
        *,
        access_token_ciphertext: str,
        refresh_token_ciphertext: str | None,
        token_expires_at: datetime,
        scopes: str,
    ) -> CalendarConnectionData | None:
        """更新令牌密文并恢复 active；refresh token 的保留由调用方负责。"""
        row = self._session.get(CalendarConnection, connection_id)
        if row is None:
            return None
        row.access_token_ciphertext = access_token_ciphertext
        row.refresh_token_ciphertext = refresh_token_ciphertext
        row.token_expires_at = token_expires_at
        row.scopes = scopes
        row.status = ConnectionStatus.ACTIVE.value
        row.revoked_at = None
        self._session.flush()
        return _connection_to_data(row)

    def update_connection_calendar(
        self, connection_id: int, *, calendar_id: str
    ) -> CalendarConnectionData | None:
        row = self._session.get(CalendarConnection, connection_id)
        if row is None:
            return None
        row.calendar_id = calendar_id
        self._session.flush()
        return _connection_to_data(row)

    def set_connection_status(
        self, connection_id: int, status: ConnectionStatus
    ) -> CalendarConnectionData | None:
        """切换连接状态；撤销时清空令牌密文并记录 revoked_at。"""
        row = self._session.get(CalendarConnection, connection_id)
        if row is None:
            return None
        row.status = status.value
        if status == ConnectionStatus.REVOKED:
            row.revoked_at = datetime.now(UTC)
            # 撤销后不再保留可解密的旧令牌，任何待写操作都会因连接状态被拒绝。
            row.access_token_ciphertext = None
            row.refresh_token_ciphertext = None
        self._session.flush()
        return _connection_to_data(row)

    def get_connection(self, connection_id: int) -> CalendarConnectionData | None:
        row = self._session.get(CalendarConnection, connection_id)
        return _connection_to_data(row) if row is not None else None

    def get_connection_for_workspace(
        self, workspace_id: int, connection_id: int
    ) -> CalendarConnectionData | None:
        row = self._session.scalar(
            select(CalendarConnection).where(
                CalendarConnection.workspace_id == workspace_id,
                CalendarConnection.id == connection_id,
            )
        )
        return _connection_to_data(row) if row is not None else None

    def list_connections(self, workspace_id: int) -> list[CalendarConnectionData]:
        rows = self._session.scalars(
            select(CalendarConnection)
            .where(CalendarConnection.workspace_id == workspace_id)
            .order_by(CalendarConnection.created_at, CalendarConnection.id)
        )
        return [_connection_to_data(row) for row in rows]

    def get_credentials(self, connection_id: int) -> CalendarCredentialsData | None:
        """只返回令牌密文与过期时间；解密和有效性判断由应用层完成。"""
        row = self._session.get(CalendarConnection, connection_id)
        if row is None:
            return None
        return CalendarCredentialsData(
            access_token_ciphertext=row.access_token_ciphertext,
            refresh_token_ciphertext=row.refresh_token_ciphertext,
            token_expires_at=row.token_expires_at,
        )

    def create_oauth_state(
        self,
        workspace_id: int,
        *,
        provider: str,
        state_hash: str,
        redirect_uri: str,
        expires_at: datetime,
    ) -> CalendarOAuthStateData:
        """保存 state 哈希与回调地址；原始 state 只在授权地址中出现，不落库。"""
        row = CalendarOAuthState(
            workspace_id=workspace_id,
            provider=provider,
            state_hash=state_hash,
            redirect_uri=redirect_uri,
            expires_at=expires_at,
        )
        self._session.add(row)
        self._session.flush()
        return _oauth_state_to_data(row)

    def consume_oauth_state(
        self, workspace_id: int, state_hash: str, *, now: datetime
    ) -> CalendarOAuthStateData | None:
        """命中未消费且未过期的哈希后写入 consumed_at；过期或重复返回 None。"""
        row = self._session.scalar(
            select(CalendarOAuthState).where(
                CalendarOAuthState.workspace_id == workspace_id,
                CalendarOAuthState.state_hash == state_hash,
                CalendarOAuthState.consumed_at.is_(None),
                CalendarOAuthState.expires_at > now,
            )
        )
        if row is None:
            return None
        # 消费即落库标记；同一 state 的重复回调此后都查不到可用记录。
        row.consumed_at = now
        self._session.flush()
        return _oauth_state_to_data(row)

    def create_draft(
        self,
        workspace_id: int,
        *,
        connection_id: int,
        status: DraftStatus,
        version: int,
        content_fingerprint: str,
        excluded: list[dict],
        warnings: list[dict],
        note: str | None,
        items: list[dict],
    ) -> CalendarDraftData:
        """新建草案并按传入顺序写入条目 position，读取顺序与确认顺序一致。"""
        draft = CalendarDraft(
            workspace_id=workspace_id,
            connection_id=connection_id,
            status=status.value,
            version=version,
            content_fingerprint=content_fingerprint,
            excluded=excluded,
            warnings=warnings,
            note=note,
        )
        self._session.add(draft)
        self._session.flush()
        rows = [
            CalendarDraftItem(
                draft_id=draft.id,
                position=position,
                **item,
            )
            for position, item in enumerate(items)
        ]
        self._session.add_all(rows)
        self._session.flush()
        return _draft_to_data(draft, [_item_to_data(row) for row in rows])

    def get_draft(self, draft_id: int) -> CalendarDraftData | None:
        row = self._session.get(CalendarDraft, draft_id)
        if row is None:
            return None
        return _draft_to_data(row, self.list_draft_items(row.id))

    def get_draft_for_workspace(self, workspace_id: int, draft_id: int) -> CalendarDraftData | None:
        row = self._session.scalar(
            select(CalendarDraft).where(
                CalendarDraft.workspace_id == workspace_id, CalendarDraft.id == draft_id
            )
        )
        if row is None:
            return None
        return _draft_to_data(row, self.list_draft_items(row.id))

    def lock_draft(self, draft_id: int) -> CalendarDraftData | None:
        """行锁读取草案，防止同一草案被并发执行或重试、重复写入外部事件。"""
        row = self._session.scalar(
            select(CalendarDraft).where(CalendarDraft.id == draft_id).with_for_update()
        )
        if row is None:
            return None
        return _draft_to_data(row, self.list_draft_items(row.id))

    def update_draft_confirmation(
        self,
        draft_id: int,
        *,
        status: DraftStatus,
        version: int,
        content_fingerprint: str,
        confirmed_fingerprint: str,
        confirmed_item_ids: list[int],
        confirmed_at: datetime,
        expires_at: datetime,
        note: str | None,
    ) -> CalendarDraftData | None:
        """保存确认指纹、选中条目与有效期；重确认升版本由用例传入。"""
        row = self._session.get(CalendarDraft, draft_id)
        if row is None:
            return None
        row.status = status.value
        row.version = version
        row.content_fingerprint = content_fingerprint
        row.confirmed_fingerprint = confirmed_fingerprint
        row.confirmed_item_ids = confirmed_item_ids
        row.confirmed_at = confirmed_at
        row.expires_at = expires_at
        row.note = note
        self._session.flush()
        return _draft_to_data(row, self.list_draft_items(row.id))

    def update_draft_status(self, draft_id: int, status: DraftStatus) -> CalendarDraftData | None:
        row = self._session.get(CalendarDraft, draft_id)
        if row is None:
            return None
        row.status = status.value
        self._session.flush()
        return _draft_to_data(row, self.list_draft_items(row.id))

    def list_draft_items(self, draft_id: int) -> list[CalendarDraftItemData]:
        """按 position 与编号返回条目，保证预览、确认与执行看到同一顺序。"""
        rows = self._session.scalars(
            select(CalendarDraftItem)
            .where(CalendarDraftItem.draft_id == draft_id)
            .order_by(CalendarDraftItem.position, CalendarDraftItem.id)
        )
        return [_item_to_data(row) for row in rows]

    def cancel_pending_drafts(self, connection_id: int) -> int:
        """把连接下所有未完成草案置为 cancelled，返回取消数量；已完成的不受影响。"""
        rows = self._session.scalars(
            select(CalendarDraft).where(
                CalendarDraft.connection_id == connection_id,
                CalendarDraft.status.in_(
                    [
                        DraftStatus.DRAFT.value,
                        DraftStatus.CONFIRMED.value,
                        DraftStatus.PARTIAL.value,
                        DraftStatus.FAILED.value,
                        DraftStatus.UNKNOWN.value,
                        DraftStatus.EXECUTING.value,
                    ]
                ),
            )
        )
        count = 0
        for row in rows:
            row.status = DraftStatus.CANCELLED.value
            count += 1
        self._session.flush()
        return count

    def create_operation(
        self,
        workspace_id: int,
        *,
        draft_id: int,
        item_id: int,
        operation_type: OperationType,
        idempotency_key: str,
        started_at: datetime,
    ) -> CalendarOperationData:
        """新建 pending 执行记录；幂等键唯一约束防止同一操作被写入两次。"""
        row = CalendarOperation(
            workspace_id=workspace_id,
            draft_id=draft_id,
            item_id=item_id,
            operation_type=operation_type.value,
            status=OperationStatus.PENDING.value,
            idempotency_key=idempotency_key,
            started_at=started_at,
        )
        self._session.add(row)
        self._session.flush()
        return _operation_to_data(row)

    def find_operation_by_key(self, idempotency_key: str) -> CalendarOperationData | None:
        """按幂等键查找既有执行；已成功项在重试时直接跳过。"""
        row = self._session.scalar(
            select(CalendarOperation).where(CalendarOperation.idempotency_key == idempotency_key)
        )
        return _operation_to_data(row) if row is not None else None

    def get_operation(self, operation_id: int) -> CalendarOperationData | None:
        row = self._session.get(CalendarOperation, operation_id)
        return _operation_to_data(row) if row is not None else None

    def update_operation_result(
        self,
        operation_id: int,
        *,
        status: OperationStatus,
        external_event_id: str | None,
        external_etag: str | None,
        error_category: str | None,
        error_message: str | None,
        result_payload: dict,
        finished_at: datetime,
        increment_attempt: bool = False,
    ) -> CalendarOperationData | None:
        """写入逐项执行结果；increment_attempt 为真时累加重试次数。"""
        row = self._session.get(CalendarOperation, operation_id)
        if row is None:
            return None
        row.status = status.value
        row.external_event_id = external_event_id
        row.external_etag = external_etag
        row.error_category = error_category
        row.error_message = error_message
        row.result_payload = result_payload
        row.finished_at = finished_at
        if increment_attempt:
            row.attempt += 1
        self._session.flush()
        return _operation_to_data(row)

    def list_operations(
        self, workspace_id: int, *, draft_id: int | None = None
    ) -> list[CalendarOperationData]:
        """按创建顺序返回执行记录；可按草案过滤，供界面稳定展示。"""
        stmt = select(CalendarOperation).where(CalendarOperation.workspace_id == workspace_id)
        if draft_id is not None:
            stmt = stmt.where(CalendarOperation.draft_id == draft_id)
        stmt = stmt.order_by(CalendarOperation.created_at, CalendarOperation.id)
        return [_operation_to_data(row) for row in self._session.scalars(stmt)]

    def get_active_link(
        self,
        connection_id: int,
        source_type: CalendarSourceType,
        source_id: int,
    ) -> CalendarEventLinkData | None:
        """只返回 active 映射；已删除或外部缺失的来源需要重新生成草案。"""
        row = self._session.scalar(
            select(CalendarEventLink).where(
                CalendarEventLink.connection_id == connection_id,
                CalendarEventLink.source_type == source_type.value,
                CalendarEventLink.source_id == source_id,
                CalendarEventLink.state == "active",
            )
        )
        return _link_to_data(row) if row is not None else None

    def list_links(self, connection_id: int) -> list[CalendarEventLinkData]:
        rows = self._session.scalars(
            select(CalendarEventLink)
            .where(CalendarEventLink.connection_id == connection_id)
            .order_by(CalendarEventLink.source_type, CalendarEventLink.source_id)
        )
        return [_link_to_data(row) for row in rows]

    def upsert_link(
        self,
        workspace_id: int,
        *,
        connection_id: int,
        source_type: CalendarSourceType,
        source_id: int,
        external_event_id: str,
        external_etag: str | None,
        content_fingerprint: str,
        source_snapshot: dict,
        state: str = "active",
    ) -> CalendarEventLinkData:
        """按连接与来源 upsert 唯一映射，更新外部标识、内容指纹与来源快照。"""
        row = self._session.scalar(
            select(CalendarEventLink).where(
                CalendarEventLink.connection_id == connection_id,
                CalendarEventLink.source_type == source_type.value,
                CalendarEventLink.source_id == source_id,
            )
        )
        now = datetime.now(UTC)
        if row is None:
            row = CalendarEventLink(
                workspace_id=workspace_id,
                connection_id=connection_id,
                source_type=source_type.value,
                source_id=source_id,
                external_event_id=external_event_id,
                external_etag=external_etag,
                content_fingerprint=content_fingerprint,
                source_snapshot=source_snapshot,
                state=state,
                last_synced_at=now,
            )
            self._session.add(row)
        else:
            row.external_event_id = external_event_id
            row.external_etag = external_etag
            row.content_fingerprint = content_fingerprint
            row.source_snapshot = source_snapshot
            row.state = state
            row.last_synced_at = now
        self._session.flush()
        return _link_to_data(row)

    def mark_link_deleted(
        self, connection_id: int, source_type: CalendarSourceType, source_id: int
    ) -> None:
        """外部事件删除成功后保留映射行并标记 deleted，便于追溯历史。"""
        row = self._session.scalar(
            select(CalendarEventLink).where(
                CalendarEventLink.connection_id == connection_id,
                CalendarEventLink.source_type == source_type.value,
                CalendarEventLink.source_id == source_id,
            )
        )
        if row is not None:
            row.state = "deleted"
            self._session.flush()

    def mark_link_external_missing(
        self, connection_id: int, source_type: CalendarSourceType, source_id: int
    ) -> None:
        """外部事件已不存在时标记，不删除内部任务或计划块，也不改写其内容。"""
        row = self._session.scalar(
            select(CalendarEventLink).where(
                CalendarEventLink.connection_id == connection_id,
                CalendarEventLink.source_type == source_type.value,
                CalendarEventLink.source_id == source_id,
            )
        )
        if row is not None:
            row.state = "external_missing"
            self._session.flush()
