// 学期、课程、任务、固定日程／可用时间与提醒的业务接口。
// 所有读写都由后端按当前会话工作空间隔离，前端不传 workspace 标识。

import { apiRequest } from './client'
import type {
  AvailabilitySlot,
  AvailabilitySlotInput,
  Course,
  CourseInput,
  FixedEvent,
  FixedEventInput,
  FixedEventOccurrence,
  Semester,
  SemesterInput,
  DependencyCheck,
  Reminder,
  ReminderRule,
  ReminderRuleInput,
  ReminderStatus,
  ReminderSummary,
  SplitSuggestion,
  SubtaskInput,
  Task,
  TaskInput,
  TaskProgress,
  TaskProgressChange,
  TaskRelations,
  TaskRollup,
} from './types'

/** 只省略 null／undefined／空串；false 与 0 仍会编码（例如 include_archived=false 也会发送）。 */
function queryString(params: Record<string, string | number | boolean | null | undefined>): string {
  const query = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => {
    if (value !== null && value !== undefined && value !== '') query.set(key, String(value))
  })
  const text = query.toString()
  return text ? `?${text}` : ''
}

// 归档只切换学期标记，不删除或改写课程、任务、进度与日程。
export const semestersApi = {
  list: (includeArchived = false) => apiRequest<Semester[]>(`/semesters${queryString({ include_archived: includeArchived })}`),
  create: (input: SemesterInput) => apiRequest<Semester>('/semesters', { method: 'POST', body: JSON.stringify(input) }),
  setArchived: (id: number, archived: boolean) => apiRequest<Semester>(`/semesters/${id}`, { method: 'PATCH', body: JSON.stringify({ archived }) }),
}

export const coursesApi = {
  list: (semesterId?: number) => apiRequest<Course[]>(`/courses${queryString({ semester_id: semesterId })}`),
  create: (input: CourseInput) => apiRequest<Course>('/courses', { method: 'POST', body: JSON.stringify(input) }),
  update: (id: number, input: Partial<CourseInput>) => apiRequest<Course>(`/courses/${id}`, { method: 'PATCH', body: JSON.stringify(input) }),
}

export const tasksApi = {
  list: (filters: { semesterId?: number; courseId?: number; progress?: TaskProgress } = {}) => apiRequest<Task[]>(`/tasks${queryString({ semester_id: filters.semesterId, course_id: filters.courseId, progress: filters.progress })}`),
  get: (id: number) => apiRequest<Task>(`/tasks/${id}`),
  create: (input: TaskInput) => apiRequest<Task>('/tasks', { method: 'POST', body: JSON.stringify(input) }),
  update: (id: number, input: Partial<TaskInput>) => apiRequest<Task>(`/tasks/${id}`, { method: 'PATCH', body: JSON.stringify(input) }),
  // 进度与核对状态是两套独立状态；reason 写入进度历史，逾期由截止时间与完成情况计算。
  setProgress: (id: number, progress: TaskProgress, reason: string | null) => apiRequest<Task>(`/tasks/${id}/progress`, { method: 'PATCH', body: JSON.stringify({ progress, reason }) }),
  progressHistory: (id: number) => apiRequest<TaskProgressChange[]>(`/tasks/${id}/progress-history`),
  relations: (id: number) => apiRequest<TaskRelations>(`/tasks/${id}/relations`),
  rollup: (id: number) => apiRequest<TaskRollup>(`/tasks/${id}/rollup`),
  subtasks: (id: number) => apiRequest<Task[]>(`/tasks/${id}/subtasks`),
  createSubtasks: (id: number, items: SubtaskInput[]) => apiRequest<Task[]>(`/tasks/${id}/subtasks`, { method: 'POST', body: JSON.stringify({ items }) }),
  // POST 但只读：返回可编辑的拆分建议，不创建任务；需用户确认后再调 createSubtasks。
  splitSuggestions: (id: number) => apiRequest<SplitSuggestion>(`/tasks/${id}/split-suggestions`, { method: 'POST', body: JSON.stringify({}) }),
  addDependency: (id: number, dependsOnTaskId: number) => apiRequest<{ id: number }>(`/tasks/${id}/dependencies`, { method: 'POST', body: JSON.stringify({ depends_on_task_id: dependsOnTaskId }) }),
  removeDependency: (id: number, dependsOnTaskId: number) => apiRequest<void>(`/tasks/${id}/dependencies/${dependsOnTaskId}`, { method: 'DELETE' }),
  // 只读检查：返回成环分量、缺失／已取消前置与逐任务阻塞原因，不自动改任务或依赖。
  dependencyCheck: (semesterId?: number) => apiRequest<DependencyCheck>(`/tasks/dependencies/check${queryString({ semester_id: semesterId })}`),
}

export const scheduleApi = {
  listEvents: (semesterId?: number) => apiRequest<FixedEvent[]>(`/fixed-events${queryString({ semester_id: semesterId })}`),
  // 返回展开每周重复后的实例，用于周视图；时间按工作空间时区解释。
  listOccurrences: (startDate: string, endDate: string, semesterId?: number) => apiRequest<FixedEventOccurrence[]>(`/fixed-events/occurrences${queryString({ start_date: startDate, end_date: endDate, semester_id: semesterId })}`),
  createEvent: (input: FixedEventInput) => apiRequest<FixedEvent>('/fixed-events', { method: 'POST', body: JSON.stringify(input) }),
  updateEvent: (id: number, input: Partial<FixedEventInput>) => apiRequest<FixedEvent>(`/fixed-events/${id}`, { method: 'PATCH', body: JSON.stringify(input) }),
  deleteEvent: (id: number) => apiRequest<void>(`/fixed-events/${id}`, { method: 'DELETE' }),
  listSlots: () => apiRequest<AvailabilitySlot[]>('/availability-slots'),
  createSlot: (input: AvailabilitySlotInput) => apiRequest<AvailabilitySlot>('/availability-slots', { method: 'POST', body: JSON.stringify(input) }),
  deleteSlot: (id: number) => apiRequest<void>(`/availability-slots/${id}`, { method: 'DELETE' }),
}

export const remindersApi = {
  list: (filters: { unreadOnly?: boolean; status?: ReminderStatus | null } = {}) => apiRequest<Reminder[]>(
    `/reminders${queryString({
      unread_only: filters.unreadOnly ? true : null,
      status: filters.status ?? null,
    })}`,
  ),
  summary: () => apiRequest<ReminderSummary>('/reminders/summary'),
  action: (id: number, action: 'read' | 'dismiss') => apiRequest<{ id: number; status: ReminderStatus }>(
    `/reminders/${id}/action`,
    { method: 'POST', body: JSON.stringify({ action }) },
  ),
  listRules: () => apiRequest<ReminderRule[]>('/reminders/rules'),
  createRule: (input: ReminderRuleInput) => apiRequest<ReminderRule>('/reminders/rules', { method: 'POST', body: JSON.stringify(input) }),
  updateRule: (id: number, input: ReminderRuleInput) => apiRequest<ReminderRule>(`/reminders/rules/${id}`, { method: 'PATCH', body: JSON.stringify(input) }),
  deleteRule: (id: number) => apiRequest<void>(`/reminders/rules/${id}`, { method: 'DELETE' }),
}
