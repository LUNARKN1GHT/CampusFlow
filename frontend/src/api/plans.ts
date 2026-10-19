// 学习计划草案、接受、时间块移动／锁定、变化建议与局部重排。
// 草案生成与重排都不改动当前已接受计划；接受、移动、锁定用 expected_version 做乐观并发控制，过期返回 409。

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
  // conflict 是确定冲突（如固定日程重叠），risk 是基于耗时估计的风险。
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
  // checks 为已执行的检查项，not_checked 为因字段缺失等原因未执行的检查，需在界面说明限制。
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
  // draft 待接受、accepted 当前计划、superseded 已被后续计划替代。
  status: 'draft' | 'accepted' | 'superseded'
  version: number
  // 排期输入指纹：用于变化检测与重排比对，不面向用户展示。
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

export type PlanRangeType = 'today' | 'this_week' | 'two_weeks' | 'custom'

export type GeneratePlanInput = {
  semester_id?: number | null
  range_type?: PlanRangeType
  start_date?: string | null
  end_date?: string | null
  assumptions?: PlanAssumptionsInput
}

export type AdjustmentSuggestion = {
  code: string
  message: string
  task_id?: number
  field?: string
}

export type AdjustmentSummary = {
  has_plan: boolean
  has_changes: boolean
  auto_applied: boolean
  requires_confirmation: boolean
  current_plan_id: number | null
  current_plan_version: number | null
  affected_task_ids: number[]
  affected_all_tasks: boolean
  suggested_task_ids: number[]
  suggestions: AdjustmentSuggestion[]
  message: string
}

export type RescheduleInput = {
  semester_id?: number | null
  start_date?: string | null
  end_date?: string | null
  task_ids?: number[] | null
  reason?: string | null
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

// 生成只产生新草案，不替换当前计划；时间不足时以未排入与缺口呈现，不做整空间回退。
export const plansApi = {
  generate: (input: GeneratePlanInput = {}) =>
    apiRequest<PlanDraft>('/plans/drafts', { method: 'POST', body: JSON.stringify(input) }),
  list: (semesterId?: number) =>
    apiRequest<PlanDraft[]>(`/plans/drafts${semesterId ? `?semester_id=${semesterId}` : ''}`),
  get: (id: number) => apiRequest<PlanDraft>(`/plans/drafts/${id}`),
  // 接受前校验版本与是否已有更新的已接受计划；返回 409 时需刷新后重新操作。
  accept: (id: number, expectedVersion: number) =>
    apiRequest<PlanDraft>(`/plans/drafts/${id}/accept`, {
      method: 'POST',
      body: JSON.stringify({ expected_version: expectedVersion }),
    }),
  // 没有当前计划时返回 null，不自动生成。
  current: (semesterId?: number) =>
    apiRequest<PlanDraft | null>(`/plans/current${semesterId ? `?semester_id=${semesterId}` : ''}`),
  conflicts: (id: number) => apiRequest<PlanConflict[]>(`/plans/drafts/${id}/conflicts`),
  overview: (semesterId?: number) =>
    apiRequest<PlanOverview>(`/plans/overview${semesterId ? `?semester_id=${semesterId}` : ''}`),
  // 只移动计划时间块，不改任务原始截止；版本过期时后端拒绝，调用方需重新加载草案再操作。
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
  // 只读变化检测：auto_applied=false，仅返回建议与受影响任务。
  adjustments: (semesterId?: number) =>
    apiRequest<AdjustmentSummary>(`/plans/adjustments${semesterId ? `?semester_id=${semesterId}` : ''}`),
  // 仅在用户请求后生成待接受草案，接受前当前计划不变。
  createAdjustmentDraft: (input: RescheduleInput = {}) =>
    apiRequest<PlanDraft>('/plans/adjustments/draft', {
      method: 'POST',
      body: JSON.stringify(input),
    }),
  // 保留锁定块与范围外时间块，只重排范围内未锁定任务；结果同样需要用户接受。
  reschedule: (input: RescheduleInput = {}) =>
    apiRequest<PlanDraft>('/plans/reschedule', {
      method: 'POST',
      body: JSON.stringify(input),
    }),
}
