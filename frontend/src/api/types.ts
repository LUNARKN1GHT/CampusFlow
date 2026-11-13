// 共享的后端请求／响应类型：字段与 /api/v1 契约一一对应，枚举取值由后端产生。
// 这里只声明结构，不做运行时校验；状态判断以后端返回值与 allowed_* 字段为准。

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

// 任务执行进度；与后端候选核对状态是两条独立状态轴，不可互相写入。
export type TaskProgress = 'not_started' | 'in_progress' | 'blocked' | 'done' | 'cancelled'
export type TaskPriority = 'low' | 'medium' | 'high'
export type DueSourceType = 'user' | 'document' | 'inferred'

export type Task = {
  id: number
  course_id: number | null
  // none 表示手工创建；invalidated 表示来源资料已删除，任务保留但引用不可再验证（归档不置为失效）。
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
  // 列表接口只对有子任务的父任务返回汇总；卡片与详情共用这一口径。
  subtask_rollup?: TaskRollup | null
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
  // 没有可估算工时时为 null，界面不应显示为 0%。
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

// 拆分建议只读：suggestion／editable 均为建议语义，确认后才批量创建子任务。
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
  // valid=false 表示存在阻塞项（成环、缺失或已取消前置）；检查只读，不修改任务或依赖。
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

// 每周重复展开后的单个实例，不是可编辑的存储对象；编辑仍针对 FixedEvent。
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
  // 0 表示周一、6 表示周日；时段按工作空间时区解释且不跨午夜。
  day_of_week: number
  start_time: string
  end_time: string
}

export type SemesterInput = Pick<Semester, 'name' | 'start_date' | 'end_date'>
export type CourseInput = Omit<Course, 'id'>
export type TaskInput = Pick<Task, 'course_id' | 'title' | 'description' | 'due_date' | 'due_time' | 'target_date' | 'due_source' | 'due_note' | 'priority' | 'estimated_minutes' | 'remaining_minutes'>
export type FixedEventInput = Omit<FixedEvent, 'id'>
export type AvailabilitySlotInput = Omit<AvailabilitySlot, 'id'>

export type CurriculumPlan = {
  id: number
  title: string
  major: string
  grade_year: number
  version_label: string
  credit_limit_per_term: number | null
  source_material_id: number | null
  source_version_id: number | null
  created_at: string
  updated_at: string
}

export type LearnerProfile = {
  workspace_id: number
  major: string | null
  grade_year: number | null
  curriculum_plan_id: number | null
  missing_fields: string[]
  current_plan: CurriculumPlan | null
}

export type SwitchPreview = {
  current_plan: CurriculumPlan | null
  target_plan: CurriculumPlan
  relation_count: number
  course_count: number
  study_record_count: number
  note: string
}

export type CurriculumCourse = {
  id: number
  curriculum_plan_id: number
  name: string
  code: string | null
  credits: number | null
  offered_terms: Array<'fall' | 'spring' | 'summer' | 'winter'>
  source_candidate_id: number | null
  created_at: string
  updated_at: string
}

export type CourseRelation = {
  id: number
  curriculum_plan_id: number
  from_course_id: number
  to_course_id: number
  relation_type: 'prerequisite' | 'corequisite' | 'recommended'
  condition_text: string | null
  source_candidate_id: number | null
  created_by: string
  created_at: string
  updated_at: string
}

export type CourseAlias = {
  id: number
  curriculum_plan_id: number
  curriculum_course_id: number
  alias: string
  class_name: string | null
  semester_id: number | null
  created_at: string
}

export type AliasResolution = {
  status: 'matched' | 'ambiguous' | 'not_found'
  matches: CurriculumCourse[]
  matched_by: 'name' | 'alias' | null
  note: string
}

export type StudyStatus = 'completed' | 'in_progress' | 'planned'

export type CourseStudyRecord = {
  id: number
  curriculum_plan_id: number
  curriculum_course_id: number
  semester_id: number | null
  semester_name: string | null
  status: StudyStatus
  grade: string | null
  credits_earned: number | null
  note: string | null
  created_at: string
  updated_at: string
}

export type CourseStudyStatusEntry = {
  curriculum_course_id: number
  course_name: string
  course_code: string | null
  status: StudyStatus | 'unknown'
  record_count: number
  repeat_record_count: number
  completed_semester_ids: number[]
  in_progress_semester_ids: number[]
  planned_semester_ids: number[]
  note: string
}

export type CourseStudyStatusSummary = {
  plan_id: number
  courses: CourseStudyStatusEntry[]
  completed_count: number
  in_progress_count: number
  planned_count: number
  unknown_count: number
  note: string
}

export type CourseGraphProblem = {
  code: string
  message: string
  course_ids: number[]
  relation_ids: number[]
  path: number[]
  candidate_ids: number[]
}

export type CourseGraphNode = {
  id: number
  name: string
  code: string | null
  credits: number | null
  offered_terms: string[]
  study_status: string
  record_count: number
}

export type CourseGraphEvidence = {
  chunk_id: number
  quote: string
  field: string
  version_id: number
  material_id: number | null
}

export type CourseGraphEdge = {
  id: number
  from_course_id: number
  to_course_id: number
  relation_type: CourseRelation['relation_type']
  condition_text: string | null
  source_candidate_id: number | null
  evidence: CourseGraphEvidence[]
}

export type CourseGraph = {
  plan_id: number
  plan_version: string
  nodes: CourseGraphNode[]
  edges: CourseGraphEdge[]
  cycles: CourseGraphProblem[]
  conflicts: CourseGraphProblem[]
  missing: CourseGraphProblem[]
  pending: CourseGraphProblem[]
  complete: boolean
  limitations: string[]
}

export type CourseOrder = {
  plan_id: number
  plan_version: string
  complete: boolean
  blocked: boolean
  levels: number[][]
  alternatives: boolean
  problems: CourseGraphProblem[]
  limitations: string[]
}

export type NextCourse = {
  course_id: number
  name: string
  study_status: string
  available: boolean
  unmet: string[]
  recommended: string[]
}

export type NextCourses = {
  plan_id: number
  plan_version: string
  complete: boolean
  blocked: boolean
  courses: NextCourse[]
  limitations: string[]
}

export type SemesterPlanInput = {
  credit_limit: number | null
  start_season: 'fall' | 'spring'
  max_terms: number | null
}

export type SemesterPlanTerm = {
  index: number
  season: 'fall' | 'spring'
  course_ids: number[]
  credits_known: number
  credits_unknown: number
  warnings: string[]
}

export type SemesterPlanUnplaced = {
  course_id: number
  name: string
  reason: string
}

export type SemesterPlan = {
  plan_id: number
  plan_version: string
  complete: boolean
  blocked: boolean
  credit_limit: number | null
  terms: SemesterPlanTerm[]
  unplaced: SemesterPlanUnplaced[]
  limitations: string[]
  assumptions: string[]
  graduation_audit: false
}

export type WorkspaceSettings = {
  workspace_id: number
  timezone: string
  daily_capacity_minutes: number
  weekly_capacity_minutes: number
  break_minutes: number
  buffer_minutes: number
}

// 桌面 AI 服务配置：只描述可用性与存储后端，后端不返回密钥明文或片段。
export type TextProvider = 'zhipu' | 'deepseek'

export type AiSettingsStatus = {
  editable: boolean
  text_provider: TextProvider
  zhipu_configured: boolean
  deepseek_configured: boolean
  text_available: boolean
  vision_available: boolean
  embedding_available: boolean
  // environment 表示服务器模式由环境变量配置；桌面模式为实际凭据后端名。
  secret_backend: string
  restart_required: boolean
  warnings: string[]
}

export type MaterialSourceType = 'pdf' | 'image' | 'text' | 'word' | 'web'
// partial 表示部分解析／索引成功，未覆盖范围必须显式提示，不能当作完整可用。
export type MaterialStatus = 'pending' | 'processing' | 'done' | 'partial' | 'failed'

// 原文读取与问答索引是两条独立状态轴（#555）：解析完成不代表可以问答。
export type TextReadState = 'queued' | 'reading' | 'ready' | 'partial' | 'failed'
export type IndexState = 'not_started' | 'preparing' | 'ready' | 'partial' | 'failed' | 'unknown'
export type MaterialIndexStatus = {
  state: IndexState
  // reason_code 是稳定协议值；message／detail／error 为后端已脱敏的可读说明。
  reason_code: string
  message: string
  detail: string | null
  error: string | null
  job_id: number | null
  retryable: boolean
}
export type MaterialProcessing = {
  text_state: TextReadState
  index: MaterialIndexStatus
}

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
  processing: MaterialProcessing
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
  // is_terminal 之后不再自动推进；retryable 决定界面是否提供重试入口。
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

export type MaterialUploadParams = {
  semester_id: number | null
  course_id: number | null
  class_name: string | null
  allow_duplicate: boolean
}

// 粘贴文本的显式识别状态：原文保存完成不代表问答索引或事项提取已经完成。
export type RecognitionIndexState = 'not_started' | 'queued' | 'running' | 'done' | 'partial' | 'failed'
export type RecognitionExtractionState = 'not_started' | 'running' | 'done' | 'empty' | 'failed'

export type MaterialRecognitionIndex = {
  state: RecognitionIndexState
  job_id: number | null
  searchable: boolean
  reason: string | null
  updated_at: string | null
}

export type MaterialRecognitionExtraction = {
  state: RecognitionExtractionState
  candidate_count: number
  pending_count: number
  reason: string | null
  extracted_at: string | null
}

export type MaterialRecognition = {
  material_id: number
  version_no: number | null
  chunk_count: number
  source_type: MaterialSourceType
  archived: boolean
  supported: boolean
  can_start: boolean
  blocked_reason: string | null
  index: MaterialRecognitionIndex
  extraction: MaterialRecognitionExtraction
}

export type MaterialRecognitionStart = MaterialRecognition & {
  started: boolean
  message: string
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
  // id 为 null 且 builtin=true 表示尚未保存的内置默认规则，首次保存时才创建记录。
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
