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

export const calendarApi = {
  listConnections: () => apiRequest<CalendarConnection[]>('/calendar/connections'),
  startOAuth: () => apiRequest<OAuthStart>('/calendar/oauth/start'),
  revoke: (connectionId: number) =>
    apiRequest<CalendarConnection>(`/calendar/connections/${connectionId}`, { method: 'DELETE' }),
  createDraft: (connectionId: number, source: DraftSource) =>
    apiRequest<CalendarDraft>('/calendar/drafts', {
      method: 'POST',
      body: JSON.stringify({ connection_id: connectionId, source }),
    }),
  getDraft: (draftId: number) => apiRequest<CalendarDraft>(`/calendar/drafts/${draftId}`),
  confirm: (draftId: number, contentFingerprint: string, itemIds: number[]) =>
    apiRequest<CalendarDraft>(`/calendar/drafts/${draftId}/confirm`, {
      method: 'POST',
      body: JSON.stringify({
        content_fingerprint: contentFingerprint,
        item_ids: itemIds,
        note: '用户在日历页面确认写入',
      }),
    }),
  execute: (draftId: number, confirmationFingerprint: string) =>
    apiRequest<ExecutionResult>(`/calendar/drafts/${draftId}/execute`, {
      method: 'POST',
      body: JSON.stringify({ confirmation_fingerprint: confirmationFingerprint }),
    }),
  retry: (draftId: number, confirmationFingerprint: string) =>
    apiRequest<ExecutionResult>(`/calendar/drafts/${draftId}/retry`, {
      method: 'POST',
      body: JSON.stringify({ confirmation_fingerprint: confirmationFingerprint }),
    }),
  listOperations: (draftId?: number) =>
    apiRequest<CalendarOperation[]>(
      draftId === undefined ? '/calendar/operations' : `/calendar/operations?draft_id=${draftId}`,
    ),
  reconcile: (operationId: number) =>
    apiRequest<CalendarOperation>(`/calendar/operations/${operationId}/reconcile`, {
      method: 'POST',
    }),
  differences: (connectionId: number) =>
    apiRequest<SyncDifferences>(`/calendar/differences?connection_id=${connectionId}`),
  differenceDraft: (connectionId: number) =>
    apiRequest<CalendarDraft>('/calendar/differences/draft', {
      method: 'POST',
      body: JSON.stringify({ connection_id: connectionId }),
    }),
}
