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
  created_at: string
  updated_at: string
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
