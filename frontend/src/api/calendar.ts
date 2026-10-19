// 外部日历（Google Calendar）连接与写入流程：连接 → 草案 → 确认 → 执行 → 对账／重试 → 差异。
// 草案预览不调用供应商；写入必须经过确认指纹，执行与重试都只认确认时签发的指纹。

import { apiRequest } from './client'

export type CalendarConnection = {
  id: number
  provider: string
  account_email: string
  calendar_id: string
  status: 'active' | 'revoked' | 'expired'
  scopes: string
  token_expires_at: string | null
  created_at: string
  updated_at: string
  revoked_at: string | null
}

export type OAuthStart = {
  authorization_url: string
  expires_at: string
}

export type DraftItem = {
  id: number
  operation_type: 'create' | 'update' | 'delete'
  source_type: 'task' | 'plan_block'
  source_id: number
  title: string
  starts_at: string
  ends_at: string
  timezone: string
  date_only: boolean
  location: string | null
  external_event_id: string | null
  item_fingerprint: string
}

export type CalendarDraft = {
  id: number
  connection_id: number
  status: 'draft' | 'confirmed' | 'executing' | 'completed' | 'partial' | 'failed' | 'unknown' | 'cancelled' | 'expired'
  version: number
  content_fingerprint: string
  confirmed_fingerprint: string | null
  confirmed_item_ids: number[]
  confirmed_at: string | null
  expires_at: string | null
  excluded: Array<Record<string, unknown> & { message?: string; title?: string; code?: string }>
  warnings: Array<Record<string, unknown> & { message?: string; code?: string }>
  note: string | null
  created_at: string
  updated_at: string
  items: DraftItem[]
}

export type CalendarOperation = {
  id: number
  draft_id: number
  item_id: number
  operation_type: 'create' | 'update' | 'delete'
  status: 'pending' | 'success' | 'failed' | 'unknown' | 'skipped'
  external_event_id: string | null
  error_category: string | null
  error_message: string | null
  attempt: number
  result_payload: Record<string, unknown>
  started_at: string | null
  finished_at: string | null
}

export type ExecutionItem = {
  item: DraftItem
  operation: CalendarOperation | null
}

export type ExecutionResult = {
  draft: CalendarDraft
  items: ExecutionItem[]
  success: number
  failed: number
  unknown: number
}

export type SyncDifference = {
  operation_type: 'create' | 'update' | 'delete'
  source_type: 'task' | 'plan_block'
  source_id: number
  title: string
  starts_at: string
  ends_at: string
  timezone: string
  date_only: boolean
  location: string | null
  external_event_id: string | null
  reason_code: string
  reason: string
}

export type SyncDifferences = {
  connection: CalendarConnection
  differences: SyncDifference[]
  can_create_draft: boolean
  note: string
}

export type DraftSource =
  | { kind: 'tasks'; task_ids: number[] }
  | { kind: 'plan'; plan_id: number }

// 授权链接一次性且短时有效，过期需重新发起；前端不接触供应商令牌。
export const calendarApi = {
  listConnections: () => apiRequest<CalendarConnection[]>('/calendar/connections'),
  startOAuth: () => apiRequest<OAuthStart>('/calendar/oauth/start'),
  // 撤销后待写入操作失效，内部任务与计划不受影响。
  revoke: (connectionId: number) =>
    apiRequest<CalendarConnection>(`/calendar/connections/${connectionId}`, { method: 'DELETE' }),
  // 只接受显式选择的任务或一个已接受计划；不调用供应商，被排除项与警告随草案返回。
  createDraft: (connectionId: number, source: DraftSource) =>
    apiRequest<CalendarDraft>('/calendar/drafts', {
      method: 'POST',
      body: JSON.stringify({ connection_id: connectionId, source }),
    }),
  getDraft: (draftId: number) => apiRequest<CalendarDraft>(`/calendar/drafts/${draftId}`),
  // content_fingerprint 必须等于当前草案重算结果，item_ids 限定本次授权范围；草案变了返回 409。
  confirm: (draftId: number, contentFingerprint: string, itemIds: number[]) =>
    apiRequest<CalendarDraft>(`/calendar/drafts/${draftId}/confirm`, {
      method: 'POST',
      body: JSON.stringify({
        content_fingerprint: contentFingerprint,
        item_ids: itemIds,
        note: '用户在日历页面确认写入',
      }),
    }),
  // 执行需带确认响应里的 confirmed_fingerprint；内部任务或连接变化后指纹失效，需重新确认。
  execute: (draftId: number, confirmationFingerprint: string) =>
    apiRequest<ExecutionResult>(`/calendar/drafts/${draftId}/execute`, {
      method: 'POST',
      body: JSON.stringify({ confirmation_fingerprint: confirmationFingerprint }),
    }),
  // 只重试失败、未知或尚未执行的选中项；已成功项不会重复调用供应商。
  retry: (draftId: number, confirmationFingerprint: string) =>
    apiRequest<ExecutionResult>(`/calendar/drafts/${draftId}/retry`, {
      method: 'POST',
      body: JSON.stringify({ confirmation_fingerprint: confirmationFingerprint }),
    }),
  listOperations: (draftId?: number) =>
    apiRequest<CalendarOperation[]>(
      draftId === undefined ? '/calendar/operations' : `/calendar/operations?draft_id=${draftId}`,
    ),
  // 超时等未知结果按幂等键向供应商核对，并把核对结果写回执行记录。
  reconcile: (operationId: number) =>
    apiRequest<CalendarOperation>(`/calendar/operations/${operationId}/reconcile`, {
      method: 'POST',
    }),
  differences: (connectionId: number) =>
    apiRequest<SyncDifferences>(`/calendar/differences?connection_id=${connectionId}`),
  // 差异只生成新的更新／删除草案，仍需用户逐项确认后才写入。
  differenceDraft: (connectionId: number) =>
    apiRequest<CalendarDraft>('/calendar/differences/draft', {
      method: 'POST',
      body: JSON.stringify({ connection_id: connectionId }),
    }),
}
