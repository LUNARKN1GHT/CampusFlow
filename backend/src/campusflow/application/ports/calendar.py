"""M9 外部日历端口：供应商适配器与持久化边界。

应用层只依赖这里的协议；Google 与本地模拟实现位于 infrastructure/calendar。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from campusflow.domain.calendar import (
    CalendarEventPayload,
    CalendarSourceType,
    ConnectionStatus,
    DraftStatus,
    OperationStatus,
    OperationType,
)


@dataclass(frozen=True)
class OAuthTokenSet:
    """供应商返回的令牌集。端口只传输明文，加密与落库由应用层负责。"""

    access_token: str
    refresh_token: str | None
    expires_at: datetime
    account_email: str | None
    scopes: str


@dataclass(frozen=True)
class ProviderEventResult:
    """供应商侧事件结果；existed=True 表示按幂等键对账命中既有事件。"""

    external_event_id: str
    etag: str | None
    html_link: str | None = None
    existed: bool = False


class CalendarProvider(Protocol):
    """外部日历供应商；所有方法只接收已解密的令牌。

    适配器不落库、不写日志中的令牌；明确失败抛 CalendarProviderError，
    写入请求可能已生效但结果未知时抛 CalendarUnknownResult，供调用方先对账。
    """

    name: str

    def authorization_url(self, *, state: str, redirect_uri: str) -> str:
        """拼接授权地址；state 是调用方生成的一次性随机值，适配器不保存。"""
        ...

    def exchange_code(self, *, code: str, redirect_uri: str) -> OAuthTokenSet:
        """用一次性授权码换取令牌；账号邮箱允许为空，不能凭授权码猜测身份。"""
        ...

    def refresh(self, *, refresh_token: str) -> OAuthTokenSet:
        """刷新访问令牌；失败抛供应商错误，调用方据此把连接标记为过期。"""
        ...

    def revoke(self, *, token: str) -> None:
        """吊销令牌；失败抛错，本地断开不依赖该调用成功。"""
        ...

    def ensure_calendar(self, *, access_token: str, timezone: str) -> str:
        """创建应用专属日历并返回标识；调用方只应在首次连接时调用一次。"""
        ...

    def create_event(
        self, *, access_token: str, calendar_id: str, event: CalendarEventPayload
    ) -> ProviderEventResult:
        """创建单个事件；payload 携带操作幂等键，供写入超时后按 key 查找对账。"""
        ...

    def update_event(
        self,
        *,
        access_token: str,
        calendar_id: str,
        external_event_id: str,
        etag: str | None,
        event: CalendarEventPayload,
    ) -> ProviderEventResult:
        """按事件标识更新；etag 非空时用于并发保护，冲突按供应商错误上报。"""
        ...

    def delete_event(
        self,
        *,
        access_token: str,
        calendar_id: str,
        external_event_id: str,
        etag: str | None,
    ) -> None:
        """删除单个事件；供应商侧已不存在时按 not_found 错误上报，由对账处理。"""
        ...

    def find_event(
        self, *, access_token: str, calendar_id: str, operation_key: str
    ) -> ProviderEventResult | None:
        """按操作幂等键查找事件，供写入超时后对账；None 表示供应商侧未找到。"""
        ...


@dataclass
class CalendarConnectionData:
    id: int
    workspace_id: int
    provider: str
    account_email: str
    calendar_id: str
    status: ConnectionStatus
    scopes: str
    token_expires_at: datetime | None
    created_at: datetime
    updated_at: datetime
    revoked_at: datetime | None = None


@dataclass(frozen=True)
class CalendarCredentialsData:
    access_token_ciphertext: str | None
    refresh_token_ciphertext: str | None
    token_expires_at: datetime | None


@dataclass
class CalendarOAuthStateData:
    id: int
    workspace_id: int
    provider: str
    state_hash: str
    redirect_uri: str
    expires_at: datetime
    consumed_at: datetime | None
    created_at: datetime


@dataclass
class CalendarDraftItemData:
    id: int
    draft_id: int
    operation_type: OperationType
    source_type: CalendarSourceType
    source_id: int
    title: str
    starts_at: datetime
    ends_at: datetime
    timezone: str
    date_only: bool
    location: str | None
    target_calendar_id: str
    external_event_id: str | None
    external_etag: str | None
    item_fingerprint: str
    content_fingerprint: str
    position: int


@dataclass
class CalendarDraftData:
    id: int
    workspace_id: int
    connection_id: int
    status: DraftStatus
    version: int
    content_fingerprint: str
    confirmed_fingerprint: str | None
    confirmed_at: datetime | None
    expires_at: datetime | None
    excluded: list[dict]
    warnings: list[dict]
    note: str | None
    created_at: datetime
    updated_at: datetime
    items: list[CalendarDraftItemData] = field(default_factory=list)
    confirmed_item_ids: list[int] = field(default_factory=list)


@dataclass
class CalendarOperationData:
    id: int
    workspace_id: int
    draft_id: int
    item_id: int
    operation_type: OperationType
    status: OperationStatus
    idempotency_key: str
    external_event_id: str | None
    external_etag: str | None
    error_category: str | None
    error_message: str | None
    attempt: int
    result_payload: dict
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime


@dataclass
class CalendarEventLinkData:
    id: int
    workspace_id: int
    connection_id: int
    source_type: CalendarSourceType
    source_id: int
    external_event_id: str
    external_etag: str | None
    content_fingerprint: str
    source_snapshot: dict
    state: str
    last_synced_at: datetime | None
    created_at: datetime
    updated_at: datetime


class CalendarStore(Protocol):
    """日历连接、草案与执行记录的持久化边界。

    方法只 flush、不提交；用例在确认、执行等边界显式 commit。带 workspace_id
    的查询在同一条 SQL 中限定空间；仅按 ID 的读取不校验归属，调用方负责核对。
    """

    def find_connection(
        self, workspace_id: int, provider: str, account_email: str
    ) -> CalendarConnectionData | None:
        """同一空间、供应商与账号已有连接时返回它，供重复授权复用。"""
        ...

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
    ) -> CalendarConnectionData: ...

    def update_connection_tokens(
        self,
        connection_id: int,
        *,
        access_token_ciphertext: str,
        refresh_token_ciphertext: str | None,
        token_expires_at: datetime,
        scopes: str,
    ) -> CalendarConnectionData | None: ...

    def update_connection_calendar(
        self, connection_id: int, *, calendar_id: str
    ) -> CalendarConnectionData | None: ...

    def set_connection_status(
        self, connection_id: int, status: ConnectionStatus
    ) -> CalendarConnectionData | None: ...

    def get_connection(self, connection_id: int) -> CalendarConnectionData | None:
        """只按 ID 读取；跨空间判断由调用方完成，不能直接用于授权决策。"""
        ...

    def get_connection_for_workspace(
        self, workspace_id: int, connection_id: int
    ) -> CalendarConnectionData | None:
        """同时限定空间与连接 ID；跨空间连接按不存在处理。"""
        ...

    def list_connections(self, workspace_id: int) -> list[CalendarConnectionData]: ...

    def get_credentials(self, connection_id: int) -> CalendarCredentialsData | None:
        """返回加密后的令牌密文，端口不解密、不记录明文。"""
        ...

    def create_oauth_state(
        self,
        workspace_id: int,
        *,
        provider: str,
        state_hash: str,
        redirect_uri: str,
        expires_at: datetime,
    ) -> CalendarOAuthStateData:
        """只保存 state 的哈希；明文 state 仅在浏览器与回调之间传递。"""
        ...

    def consume_oauth_state(
        self, workspace_id: int, state_hash: str, *, now: datetime
    ) -> CalendarOAuthStateData | None:
        """原子消费一次性 state；已消费或已过期返回 None，不能重放。"""
        ...

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
    ) -> CalendarDraftData: ...

    def get_draft(self, draft_id: int) -> CalendarDraftData | None:
        """只按 ID 读取；调用方必须先确认草案属于当前空间。"""
        ...

    def get_draft_for_workspace(self, workspace_id: int, draft_id: int) -> CalendarDraftData | None:
        """同时限定空间与草案 ID；跨空间草案按不存在处理。"""
        ...

    def lock_draft(self, draft_id: int) -> CalendarDraftData | None:
        """取行锁直到事务结束，串行化同一草案的确认与执行请求。"""
        ...

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
    ) -> CalendarDraftData | None: ...

    def update_draft_status(
        self, draft_id: int, status: DraftStatus
    ) -> CalendarDraftData | None: ...

    def list_draft_items(self, draft_id: int) -> list[CalendarDraftItemData]: ...

    def cancel_pending_drafts(self, connection_id: int) -> int:
        """撤销连接时取消未完成草案，返回数量；已成功条目不受影响。"""
        ...

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
        """登记一条待执行操作；幂等键唯一，调用方应先按键查找已有记录。"""
        ...

    def find_operation_by_key(self, idempotency_key: str) -> CalendarOperationData | None:
        """按幂等键定位执行记录，供重试与超时后对账，不重复写外部事件。"""
        ...

    def get_operation(self, operation_id: int) -> CalendarOperationData | None: ...

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
        """写回单项执行结果；increment_attempt 只在实际重试时递增，避免虚计次数。"""
        ...

    def list_operations(
        self, workspace_id: int, *, draft_id: int | None = None
    ) -> list[CalendarOperationData]: ...

    def get_active_link(
        self,
        connection_id: int,
        source_type: CalendarSourceType,
        source_id: int,
    ) -> CalendarEventLinkData | None:
        """读取某个内部任务/计划块在目标日历上的有效事件映射。"""
        ...

    def list_links(self, connection_id: int) -> list[CalendarEventLinkData]: ...

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
        """同一连接与来源只保留一条映射；外部标识变化时原位更新快照。"""
        ...

    def mark_link_deleted(
        self, connection_id: int, source_type: CalendarSourceType, source_id: int
    ) -> None:
        """只改映射状态，不删记录，便于重连后判断同步差异。"""
        ...

    def mark_link_external_missing(
        self, connection_id: int, source_type: CalendarSourceType, source_id: int
    ) -> None:
        """外部事件被手工删除时标记失效，后续差异不再建议更新它。"""
        ...

    def commit(self) -> None:
        """提交当前事务；用例在确认/执行结束时调用，草案与逐项结果一并落库。"""
        ...

    def flush(self) -> None:
        """只把变更发送到数据库，不结束事务；失败后由调用方回滚。"""
        ...
