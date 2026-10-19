// 任务清单 CSV 与日历 ICS 导出：必须先预览，下载携带预览返回的范围指纹。
// 只导出显式选择的任务或当前已接受计划，不做整空间回退；范围变化后指纹不一致由后端返回 409。

import { apiRequest, apiRequestFile, type RequestedFile } from './client'

export type ExportMode = 'tasks' | 'plan'
export type ExportFormat = 'csv' | 'ics'

export type ExportOptions = {
  include_source_hints: boolean
  include_fixed_events: boolean
}

/** 预览与下载共用同一份范围；下载时追加 scope_fingerprint（A017 §4）。 */
export type ExportRequest = {
  mode: ExportMode
  format: ExportFormat
  task_ids: number[] | null
  plan_id: number | null
  options: ExportOptions
  scope_fingerprint?: string | null
}

export type ExportScope = {
  task_ids: number[]
  plan_id: number | null
  plan_version: number | null
  semester_id: number | null
  range_start: string | null
  range_end: string | null
}

export type ExportDateRange = {
  start: string | null
  end: string | null
  has_date_only: boolean
}

export type ExportCounts = {
  date_only: number
  timed: number
  no_due: number
  scheduled: number
  unscheduled: number
  source_invalidated: number
  cancelled: number
}

export type ExportNotice = {
  code: string
  count: number
  message: string
}

export type ExportItem = {
  task_id: number
  title: string
  course_id: number | null
  course_name: string | null
  due_date: string | null
  due_time: string | null
  due_precision: string
  due_source: string
  target_date: string | null
  priority: string
  progress: string
  estimated_minutes: number | null
  remaining_minutes: number | null
  source_state: string
  source_title: string | null
  source_version_no: number | null
  source_locator: string | null
  included: boolean
  exclusion_reason: string | null
}

export type ExportPreview = {
  mode: ExportMode
  format: ExportFormat
  scope: ExportScope
  timezone: string
  item_count: number
  row_count: number
  /** ICS 预览的事件数；未提供时回退到 row_count。 */
  event_count?: number
  date_range: ExportDateRange
  counts: ExportCounts
  excluded: ExportNotice[]
  warnings: ExportNotice[]
  items: ExportItem[]
  scope_fingerprint: string
  can_download: boolean
}

// 计划模式只提供 ICS；任务模式 CSV／ICS 均可，具体限制由后端按模式校验。
export const exportsApi = {
  preview: (input: ExportRequest) =>
    apiRequest<ExportPreview>('/exports/preview', {
      method: 'POST',
      body: JSON.stringify(input),
    }),
  // 下载时按同一份范围重算并比对 scope_fingerprint；缺失指纹返回 400，不一致返回 409。
  download: (input: ExportRequest): Promise<RequestedFile> =>
    apiRequestFile('/exports', {
      method: 'POST',
      body: JSON.stringify(input),
    }),
}
