export type Semester = {
  id: number
  name: string
  start_date: string
  end_date: string
  archived: boolean
}

export type Course = {
  id: number
  semester_id: number
  name: string
  code: string | null
  teacher: string | null
  class_name: string | null
}

export type TaskProgress = 'not_started' | 'in_progress' | 'blocked' | 'done' | 'cancelled'
export type TaskPriority = 'low' | 'medium' | 'high'
export type DueSourceType = 'user' | 'document' | 'inferred'

export type Task = {
  id: number
  course_id: number | null
  source_state: 'available' | 'invalidated' | 'none'
  source_invalidated_at: string | null
  source_invalidated_note: string | null
  title: string
  description: string | null
  due_date: string | null
  due_time: string | null
  target_date: string | null
  due_source: DueSourceType
  due_note: string | null
  overdue: boolean
  progress: TaskProgress
  priority: TaskPriority
  estimated_minutes: number | null
  remaining_minutes: number | null
  parent_task_id: number | null
  created_at: string
  updated_at: string
}

export type TaskLink = {
  id: number
  title: string
  progress: TaskProgress
  due_date: string | null
  parent_task_id: number | null
}

export type TaskRollup = {
  subtask_count: number
  estimated_minutes: number
  remaining_minutes: number
  missing_duration_count: number
  progress_counts: Record<string, number>
  percent_complete: number | null
}

export type TaskRelations = {
  task_id: number
  parent: TaskLink | null
  subtasks: TaskLink[]
  prerequisites: TaskLink[]
  dependents: TaskLink[]
  rollup: TaskRollup
}

export type SubtaskInput = {
  title: string
  priority?: TaskPriority
  estimated_minutes?: number | null
  remaining_minutes?: number | null
  due_date?: string | null
}

export type SplitSuggestion = {
  parent_task_id: number
  parent_title: string
  suggestion: boolean
  editable: boolean
  note: string
  items: Array<{
    title: string
    estimated_minutes: number | null
    remaining_minutes: number | null
    suggested: boolean
  }>
}

export type DependencyCheck = {
  semester_id: number | null
  valid: boolean
  checked_tasks: number
  checked_edges: number
  cycles: Array<{ task_ids: number[]; titles: string[]; message: string }>
  missing_prerequisites: Array<Record<string, unknown> & { task_id: number; title: string; code: string; message: string }>
  cancelled_prerequisites: Array<Record<string, unknown> & { task_id: number; title: string; code: string; message: string }>
  blocked_tasks: Array<{
    task_id: number
    title: string
    reasons: Array<{ code: string; message: string }>
  }>
}

export type TaskProgressChange = {
  id: number
  task_id: number
  from_progress: TaskProgress
  to_progress: TaskProgress
  reason: string | null
  previous_remaining_minutes: number | null
  new_remaining_minutes: number | null
  changed_at: string
}

export type EventRecurrence = 'none' | 'weekly'
export type FixedEvent = {
  id: number
  course_id: number | null
  title: string
  starts_at: string
  ends_at: string
  location: string | null
  recurrence: EventRecurrence
  repeat_until: string | null
}

export type FixedEventOccurrence = {
  source_event_id: number
  course_id: number | null
  title: string
  starts_at: string
  ends_at: string
  location: string | null
}

export type AvailabilitySlot = {
  id: number
  day_of_week: number
  start_time: string
  end_time: string
}

export type SemesterInput = Pick<Semester, 'name' | 'start_date' | 'end_date'>
export type CourseInput = Omit<Course, 'id'>
export type TaskInput = Pick<Task, 'course_id' | 'title' | 'description' | 'due_date' | 'due_time' | 'target_date' | 'due_source' | 'due_note' | 'priority' | 'estimated_minutes' | 'remaining_minutes'>
export type FixedEventInput = Omit<FixedEvent, 'id'>
export type AvailabilitySlotInput = Omit<AvailabilitySlot, 'id'>

export type WorkspaceSettings = {
  workspace_id: number
  timezone: string
  daily_capacity_minutes: number
  weekly_capacity_minutes: number
  break_minutes: number
  buffer_minutes: number
}

export type MaterialSourceType = 'pdf' | 'image' | 'text' | 'word' | 'web'
export type MaterialStatus = 'pending' | 'processing' | 'done' | 'partial' | 'failed'

export type Material = {
  id: number
  semester_id: number | null
  course_id: number | null
  class_name: string | null
  title: string
  publisher: string | null
  published_at: string | null
  source_type: MaterialSourceType
  source_url: string | null
  status: MaterialStatus
  archived: boolean
  imported_at: string
}

export type MaterialVersion = {
  id: number
  material_id: number
  version_no: number
  note: string | null
  created_at: string
}

export type SourceChunk = {
  id: number
  version_id: number
  seq: number
  locator_type: string
  locator_value: string
  text: string
  page: number | null
  paragraph: number | null
  bbox: [number, number, number, number] | null
  confidence: number | null
  table_group: string | null
}

export type JobStage = 'parse' | 'extract' | 'embed'
export type JobStatus = 'pending' | 'running' | 'done' | 'partial' | 'failed'

export type ProcessingJob = {
  id: number
  material_id: number
  version_id: number
  stage: JobStage
  status: JobStatus
  is_terminal: boolean
  retryable: boolean
  scope: string | null
  error_reason: string | null
  attempts: number
  enqueued_at: string | null
  started_at: string | null
  created_at: string
  updated_at: string
}

export type JobAttempt = {
  attempt_no: number
  stage: JobStage
  status: JobStatus
  scope: string | null
  error_reason: string | null
  retryable: boolean
  created_at: string
}

export type MaterialUploadItem = {
  filename: string
  material: Material | null
  error: string | null
  duplicate_of: Material | null
  job_id: number | null
}

export type WebImportInput = {
  url: string
  title: string
  publisher: string | null
  published_at: string | null
  published_text: string | null
  semester_id: number | null
  course_id: number | null
  class_name: string | null
}

export type WebImportResult = {
  material: Material
  version: MaterialVersion
  job_id: number
}

export type MaterialDeleteImpact = {
  material: Material
  version_count: number
  chunk_count: number
  linked_task_ids: number[]
  unfinished_job_count: number
  candidate_count: number
  linked_fixed_event_ids: number[]
  linked_rule_ids: number[]
  version_relation_ids: number[]
  confirm_token: string
  backup_note: string
}

export type TextImportInput = {
  title: string
  content: string
  publisher: string | null
  published_at: string | null
  semester_id: number | null
  course_id: number | null
  class_name: string | null
}

export type MaterialMetadataInput = {
  title: string
  publisher: string | null
  published_at: string | null
  source_url: string | null
  semester_id: number | null
  course_id: number | null
  class_name: string | null
}

export type MaterialUploadParams = {
  semester_id: number | null
  course_id: number | null
  class_name: string | null
  allow_duplicate: boolean
}

export type ReviewStatus = 'pending' | 'confirmed' | 'ignored' | 'superseded'
export type CandidateKind = 'task' | 'event' | 'rule'

export type CandidateReason = {
  code: string
  message: string
  field: string | null
}

export type CandidateSummary = {
  id: number
  kind: CandidateKind
  review_status: ReviewStatus
  title: string | null
  course_id: number | null
  semester_id: number | null
  class_name: string | null
  source_material_id: number
  source_version_id: number
  version_no: number
  review_reasons: CandidateReason[]
  created_at: string
}

export type FieldOrigin = 'document' | 'user' | 'inferred' | 'suggestion'

export type CandidateEvidence = {
  chunk_id: number
  quote: string
  locator_type: string
  locator_value: string
  verifiable: boolean
}

export type CandidateFieldView = {
  name: string
  value: unknown
  origin: FieldOrigin
  effective_value: unknown
  effective_origin: FieldOrigin
  explanation: string | null
  missing_reason: string | null
  verifiable: boolean
  evidence: CandidateEvidence[]
}

export type CandidateRevision = {
  id: number
  field_name: string
  previous_value: unknown
  new_value: unknown
  previous_origin: FieldOrigin
  new_origin: FieldOrigin
  operator: string
  note: string | null
  created_at: string
}

export type CandidateReviewChange = {
  id: number
  from_status: ReviewStatus
  to_status: ReviewStatus
  operator: string
  reason: string | null
  changed_at: string
}

export type CandidateConversion = { kind: CandidateKind; object_id: number; title: string }
export type CandidateConversionResult = CandidateConversion & { created: boolean }

export type CandidateReviewDetail = {
  id: number
  kind: CandidateKind
  review_status: ReviewStatus
  created_at: string
  scope: {
    course_id: number | null
    semester_id: number | null
    class_name: string | null
    course_name: string | null
    semester_name: string | null
  }
  source: {
    material_id: number
    material_title: string
    version_id: number
    version_no: number
  }
  fields: CandidateFieldView[]
  revisions: CandidateRevision[]
  review_reasons: CandidateReason[]
  review_changes: CandidateReviewChange[]
  missing_required_fields: string[]
  allowed_transitions: ReviewStatus[]
  converted: CandidateConversion | null
}

export type DiffEvidence = {
  chunk_id: number
  quote: string
  locator_type: string
  locator_value: string
  verifiable: boolean
}

export type DiffFieldSide = {
  candidate_id: number
  source_version_id: number
  present: boolean
  value: unknown
  effective_value: unknown
  origin: FieldOrigin | null
  effective_origin: FieldOrigin | null
  verifiable: boolean
  evidence: DiffEvidence[]
}

export type DiffField = {
  name: string
  category: string
  status: 'unchanged' | 'changed' | 'added' | 'missing' | 'explicitly_removed'
  left: DiffFieldSide
  right: DiffFieldSide
}

export type DiffScope = {
  semester_id: number | null
  semester_name: string | null
  course_id: number | null
  course_name: string | null
  class_name: string | null
}

export type DiffSource = {
  candidate_id: number
  source_version_id: number
  material_id: number
  material_title: string
  version_no: number
  material_archived: boolean
  kind: CandidateKind
  review_status: ReviewStatus
  scope: DiffScope
}

export type CandidateDiff = {
  left: DiffSource
  right: DiffSource
  scope: { left: DiffScope; right: DiffScope; differences: { component: string; status: string }[] }
  fields: DiffField[]
  summary: { total: number; unchanged: number; changed: number; added: number; missing: number; explicitly_removed: number }
}

export type VersionApplicability = {
  status: 'applicable' | 'conflict' | 'requires_review'
  recommended_candidate_id: number | null
  review_required: boolean
  applied: false
  relation_id: number | null
  relation_kind: string | null
  scope_comparison: 'same' | 'different' | 'unknown'
  publication: Record<string, { material_id: number; version_id: number; publisher: string | null; published_at: string | null; published_text: string | null }>
  reasons: { code: string; message: string }[]
  diff: CandidateDiff
}

export type ImpactedBlock = {
  id: number
  draft_id: number
  draft_status: string
  title: string
  starts_at: string
  ends_at: string
  locked: boolean
}

export type ChangeImpact = {
  summary: Record<string, number>
  unsupported: { code: string; message: string }[]
  tasks: { id: number; title: string; progress: string; completed: boolean; due_date: string | null; source_state: string; plan_blocks: ImpactedBlock[] }[]
  fixed_events: { id: number; title: string; starts_at: string; ends_at: string; location: string | null }[]
  rules: { id: number; title: string }[]
}

export type VersionAcceptance = {
  id: number
  baseline_candidate_id: number
  comparison_candidate_id: number
  baseline_version_id: number
  comparison_version_id: number
  accepted_fields: string[]
  reason: string
  operator: string
  evidence_snapshot: { name: string; status: string; left: DiffFieldSide; right: DiffFieldSide }[]
  previous_review_status: string
  created_at: string
  baseline_review_status: string
  created: boolean
}

export type DuplicateSuggestion = {
  candidate_id: number
  source_version_id: number
  title: string | null
  score: number
  reasons: string[]
  matched_fields: string[]
  review_status: ReviewStatus
  scope_same: boolean
}

export type DuplicateReport = {
  candidate_id: number
  kind: CandidateKind
  suggestions: DuplicateSuggestion[]
  excluded: { candidate_id: number; code: string; message: string }[]
  auto_merge: false
}

export type MergeSource = {
  candidate_id: number
  version_id: number
  title: string | null
  review_status: ReviewStatus
  formal_kind: string
  formal_id: number | null
  progress: string | null
  plan_block_count: number
}

export type MergePreview = {
  target_candidate_id: number
  kind: CandidateKind
  target_title: string | null
  target_formal_id: number | null
  target_progress: string | null
  kept_fields: Record<string, unknown>
  sources: MergeSource[]
  token: string
  auto_merge: false
}

export type MergeRecord = {
  id: number
  target_candidate_id: number
  source_candidate_id: number
  target_formal_kind: string | null
  target_formal_id: number | null
  source_formal_id: number | null
  preserved_progress: Record<string, unknown>
  source_snapshot: Record<string, unknown>
  reason: string
  operator: string
  created_at: string
}

export type MergeResult = {
  records: MergeRecord[]
  created: boolean
  preview: MergePreview
}

export type ReminderKind = 'deadline' | 'review'
export type ReminderStatus = 'pending' | 'sent' | 'dismissed' | 'cancelled'

export type Reminder = {
  id: number
  task_id: number | null
  kind: ReminderKind
  message: string
  status: ReminderStatus
  scheduled_for: string
  sent_at: string | null
  read_at: string | null
  payload: Record<string, unknown>
}

export type ReminderSummary = {
  unread: number
  sent: number
  pending: number
  dismissed: number
  cancelled: number
  note: string
}

export type ReminderRule = {
  id: number | null
  task_id: number | null
  advance_minutes: number
  repeat_minutes: number | null
  paused: boolean
  enabled: boolean
  version: number
  is_default: boolean
  builtin: boolean
}

export type ReminderRuleInput = {
  task_id?: number | null
  advance_minutes: number
  repeat_minutes: number | null
  paused: boolean
  enabled: boolean
}
