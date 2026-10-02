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

export type Task = {
  id: number
  course_id: number | null
  title: string
  description: string | null
  due_date: string | null
  due_time: string | null
  progress: TaskProgress
  priority: TaskPriority
  estimated_minutes: number | null
  remaining_minutes: number | null
  created_at: string
  updated_at: string
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

export type AvailabilitySlot = {
  id: number
  day_of_week: number
  start_time: string
  end_time: string
}

export type SemesterInput = Pick<Semester, 'name' | 'start_date' | 'end_date'>
export type CourseInput = Omit<Course, 'id'>
export type TaskInput = Pick<Task, 'course_id' | 'title' | 'description' | 'due_date' | 'due_time' | 'priority' | 'estimated_minutes' | 'remaining_minutes'>
export type FixedEventInput = Omit<FixedEvent, 'id'>
export type AvailabilitySlotInput = Omit<AvailabilitySlot, 'id'>
