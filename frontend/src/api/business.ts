import { apiRequest } from './client'
import type {
  AvailabilitySlot,
  AvailabilitySlotInput,
  Course,
  CourseInput,
  FixedEvent,
  FixedEventInput,
  Semester,
  SemesterInput,
  Task,
  TaskInput,
  TaskProgress,
  TaskProgressChange,
} from './types'

function queryString(params: Record<string, string | number | boolean | null | undefined>): string {
  const query = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => {
    if (value !== null && value !== undefined && value !== '') query.set(key, String(value))
  })
  const text = query.toString()
  return text ? `?${text}` : ''
}

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
  list: (filters: { courseId?: number; progress?: TaskProgress } = {}) => apiRequest<Task[]>(`/tasks${queryString({ course_id: filters.courseId, progress: filters.progress })}`),
  get: (id: number) => apiRequest<Task>(`/tasks/${id}`),
  create: (input: TaskInput) => apiRequest<Task>('/tasks', { method: 'POST', body: JSON.stringify(input) }),
  update: (id: number, input: Partial<TaskInput>) => apiRequest<Task>(`/tasks/${id}`, { method: 'PATCH', body: JSON.stringify(input) }),
  setProgress: (id: number, progress: TaskProgress, reason: string | null) => apiRequest<Task>(`/tasks/${id}/progress`, { method: 'PATCH', body: JSON.stringify({ progress, reason }) }),
  progressHistory: (id: number) => apiRequest<TaskProgressChange[]>(`/tasks/${id}/progress-history`),
}

export const scheduleApi = {
  listEvents: () => apiRequest<FixedEvent[]>('/fixed-events'),
  createEvent: (input: FixedEventInput) => apiRequest<FixedEvent>('/fixed-events', { method: 'POST', body: JSON.stringify(input) }),
  updateEvent: (id: number, input: Partial<FixedEventInput>) => apiRequest<FixedEvent>(`/fixed-events/${id}`, { method: 'PATCH', body: JSON.stringify(input) }),
  deleteEvent: (id: number) => apiRequest<void>(`/fixed-events/${id}`, { method: 'DELETE' }),
  listSlots: () => apiRequest<AvailabilitySlot[]>('/availability-slots'),
  createSlot: (input: AvailabilitySlotInput) => apiRequest<AvailabilitySlot>('/availability-slots', { method: 'POST', body: JSON.stringify(input) }),
  deleteSlot: (id: number) => apiRequest<void>(`/availability-slots/${id}`, { method: 'DELETE' }),
}
