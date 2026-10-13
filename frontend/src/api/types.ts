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

export type MaterialDeleteImpact = {
  material: Material
  version_count: number
  chunk_count: number
  linked_task_ids: number[]
  unfinished_job_count: number
  candidate_count: number
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
