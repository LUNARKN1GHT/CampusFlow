import { apiRequest } from './client'

export type PlanConstraint = {
  kind: string
  text?: string
  value?: string | number | boolean
  date_only?: boolean
  risk?: boolean
}

export type PlanBlock = {
  id: number
  draft_id: number
  task_id: number | null
  kind: string
  title: string
  starts_at: string
  ends_at: string
  minutes: number
  seq: number
  locked: boolean
  reason: string
  constraint_source: PlanConstraint[]
}

export type PlanTaskDisposition = {
  task_id: number
  title: string
  status: string
  scheduled_minutes: number
  remaining_minutes: number
  reason_code: string | null
  message: string
  options: string[]
}

export type PlanGap = {
  code: string
  field: string
  task_id: number
  title: string
  message: string
}

export type PlanAssumption = {
  kind: string
  task_id?: number
  title?: string
  value?: string | number
  text: string
}

export type PlanConflict = {
  type: string
  severity: 'conflict' | 'risk'
  message: string
  suggestion: string
  participants?: Array<{ kind: string; id: number | null; title: string }>
  starts_at?: string
  ends_at?: string
  task_id?: number
  title?: string
  shortfall_minutes?: number
  due_at?: string
  estimated?: boolean
  date?: string
  planned_minutes?: number
  capacity_minutes?: number
}

export type PlanCoverage = {
  tasks_considered: number
  tasks_scheduled: number
  tasks_partial: number
  tasks_unscheduled: number
  tasks_excluded: number
  checks: string[]
  not_checked: string[]
  notes: string[]
  capacity_gap_minutes: number
  adjustment_options: string[]
}

export type PlanDraft = {
  id: number
  semester_id: number | null
  range_start: string
  range_end: string
  timezone: string
  status: 'draft' | 'accepted' | 'superseded'
  version: number
  input_fingerprint: string
  assumptions: PlanAssumption[]
  gaps: PlanGap[]
  unscheduled: PlanTaskDisposition[]
  conflicts: PlanConflict[]
  coverage: PlanCoverage
  created_at: string
  accepted_at: string | null
  blocks: PlanBlock[]
}

export type PlanAssumptionsInput = {
  default_duration_minutes?: number | null
  task_durations?: Record<number, number>
  task_due_dates?: Record<number, string>
  segment_minutes?: number | null
  buffer_minutes?: number | null
}

export type GeneratePlanInput = {
  semester_id?: number | null
  start_date?: string | null
  end_date?: string | null
  assumptions?: PlanAssumptionsInput
}

export type PlanOverview = {
  today: string
  timezone: string
  has_plan: boolean
  plan_status: string | null
  plan_version: number | null
  today_blocks: PlanBlock[]
  next_block: PlanBlock | null
  upcoming_tasks: Array<{
    id: number
    title: string
    due_date: string | null
    due_time: string | null
    priority: 'low' | 'medium' | 'high'
    progress: string
    overdue: boolean
  }>
  pending_review_count: number
  unscheduled_count: number
  conflict_count: number
}

export const plansApi = {
  generate: (input: GeneratePlanInput = {}) =>
    apiRequest<PlanDraft>('/plans/drafts', { method: 'POST', body: JSON.stringify(input) }),
  list: (semesterId?: number) =>
    apiRequest<PlanDraft[]>(`/plans/drafts${semesterId ? `?semester_id=${semesterId}` : ''}`),
  get: (id: number) => apiRequest<PlanDraft>(`/plans/drafts/${id}`),
  accept: (id: number, expectedVersion: number) =>
    apiRequest<PlanDraft>(`/plans/drafts/${id}/accept`, {
      method: 'POST',
      body: JSON.stringify({ expected_version: expectedVersion }),
    }),
  current: (semesterId?: number) =>
    apiRequest<PlanDraft | null>(`/plans/current${semesterId ? `?semester_id=${semesterId}` : ''}`),
  conflicts: (id: number) => apiRequest<PlanConflict[]>(`/plans/drafts/${id}/conflicts`),
  overview: (semesterId?: number) =>
    apiRequest<PlanOverview>(`/plans/overview${semesterId ? `?semester_id=${semesterId}` : ''}`),
  moveBlock: (id: number, startsAt: string, endsAt: string, expectedVersion: number) =>
    apiRequest<PlanDraft>(`/plans/blocks/${id}`, {
      method: 'PATCH',
      body: JSON.stringify({ starts_at: startsAt, ends_at: endsAt, expected_version: expectedVersion }),
    }),
  setLock: (id: number, locked: boolean, expectedVersion: number) =>
    apiRequest<PlanDraft>(`/plans/blocks/${id}/lock`, {
      method: 'POST',
      body: JSON.stringify({ locked, expected_version: expectedVersion }),
    }),
}
