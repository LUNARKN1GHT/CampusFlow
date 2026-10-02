<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import { coursesApi, semestersApi, tasksApi } from '../api/business'
import { errorMessage } from '../api/client'
import type { Course, Semester, Task, TaskInput, TaskPriority, TaskProgress } from '../api/types'
import ApiFeedback from '../components/ApiFeedback.vue'

const tasks = ref<Task[]>([])
const courses = ref<Course[]>([])
const semesters = ref<Semester[]>([])
const loading = ref(true)
const saving = ref(false)
const error = ref('')
const formError = ref('')
const editorOpen = ref(false)
const editing = ref<Task | null>(null)
const filters = reactive({ semesterId: '', courseId: '', progress: '' })
const form = reactive({
  title: '', description: '', course_id: '', due_date: '', due_time: '',
  priority: 'medium' as TaskPriority, estimated_minutes: '', remaining_minutes: '',
})

const courseMap = computed(() => new Map(courses.value.map((course) => [course.id, course])))
const filteredCourses = computed(() => filters.semesterId
  ? courses.value.filter((course) => course.semester_id === Number(filters.semesterId))
  : courses.value)
const visibleTasks = computed(() => tasks.value.filter((task) => {
  if (filters.courseId && task.course_id !== Number(filters.courseId)) return false
  if (filters.progress && task.progress !== filters.progress) return false
  if (filters.semesterId) {
    if (task.course_id === null) return false
    if (courseMap.value.get(task.course_id)?.semester_id !== Number(filters.semesterId)) return false
  }
  return true
}))

const progressLabels: Record<TaskProgress, string> = {
  not_started: '未开始', in_progress: '进行中', blocked: '受阻', done: '已完成', cancelled: '已取消',
}
const priorityLabels: Record<TaskPriority, string> = { low: '低', medium: '中', high: '高' }

async function load() {
  loading.value = true
  error.value = ''
  try {
    ;[tasks.value, courses.value, semesters.value] = await Promise.all([
      tasksApi.list(), coursesApi.list(), semestersApi.list(true),
    ])
  } catch (caught) {
    error.value = errorMessage(caught)
  } finally {
    loading.value = false
  }
}

function openEditor(task?: Task) {
  editing.value = task ?? null
  editorOpen.value = true
  formError.value = ''
  Object.assign(form, {
    title: task?.title ?? '', description: task?.description ?? '', course_id: task?.course_id?.toString() ?? '',
    due_date: task?.due_date ?? '', due_time: task?.due_time?.slice(0, 5) ?? '', priority: task?.priority ?? 'medium',
    estimated_minutes: task?.estimated_minutes?.toString() ?? '', remaining_minutes: task?.remaining_minutes?.toString() ?? '',
  })
}

function payload(): TaskInput {
  return {
    title: form.title,
    description: form.description || null,
    course_id: form.course_id ? Number(form.course_id) : null,
    due_date: form.due_date || null,
    due_time: form.due_time || null,
    priority: form.priority,
    estimated_minutes: form.estimated_minutes ? Number(form.estimated_minutes) : null,
    remaining_minutes: form.remaining_minutes ? Number(form.remaining_minutes) : null,
  }
}

async function saveTask() {
  saving.value = true
  formError.value = ''
  try {
    const values = payload()
    if (editing.value) {
      const updated = await tasksApi.update(editing.value.id, values)
      tasks.value[tasks.value.findIndex((task) => task.id === updated.id)] = updated
    } else {
      tasks.value.push(await tasksApi.create(values))
    }
    editorOpen.value = false
  } catch (caught) {
    formError.value = errorMessage(caught)
  } finally {
    saving.value = false
  }
}

function dueText(task: Task): string {
  if (!task.due_date) return '未设置 DDL'
  return task.due_time ? `${task.due_date} ${task.due_time.slice(0, 5)}` : `${task.due_date}（仅日期）`
}

onMounted(load)
</script>

<template>
  <main class="page-content">
    <div class="page-heading page-heading-row">
      <div><p class="eyebrow">任务与计划</p><h1>任务</h1><p class="intro">手动维护 DDL、优先级与剩余投入，不改变课程原始截止信息。</p></div>
      <button type="button" @click="openEditor()">添加任务</button>
    </div>

    <ApiFeedback v-if="error" :message="error" :busy="loading" @retry="load" />
    <template v-else>
      <section class="panel filter-bar" aria-label="任务筛选">
        <label>学期<select v-model="filters.semesterId" @change="filters.courseId = ''"><option value="">全部学期</option><option v-for="semester in semesters" :key="semester.id" :value="semester.id">{{ semester.name }}{{ semester.archived ? '（已归档）' : '' }}</option></select></label>
        <label>课程<select v-model="filters.courseId"><option value="">全部课程</option><option v-for="course in filteredCourses" :key="course.id" :value="course.id">{{ course.name }} · {{ course.class_name || '未分班' }}</option></select></label>
        <label>进度<select v-model="filters.progress"><option value="">全部进度</option><option v-for="(label, value) in progressLabels" :key="value" :value="value">{{ label }}</option></select></label>
        <span class="count-badge">{{ visibleTasks.length }} 项</span>
      </section>

      <section class="panel">
        <p v-if="loading" class="muted">正在加载任务…</p>
        <div v-else-if="visibleTasks.length" class="task-list">
          <article v-for="task in visibleTasks" :key="task.id" class="task-row">
            <div class="priority-marker" :data-priority="task.priority"></div>
            <div class="task-main">
              <div class="task-title-line"><h3>{{ task.title }}</h3><span class="status-pill">{{ progressLabels[task.progress] }}</span></div>
              <p>{{ courseMap.get(task.course_id ?? -1)?.name ?? '未关联课程' }} · {{ dueText(task) }}</p>
            </div>
            <div class="task-meta"><span>优先级 {{ priorityLabels[task.priority] }}</span><strong>{{ task.remaining_minutes ?? '—' }}<small> 分钟剩余</small></strong></div>
            <button type="button" class="button-secondary" @click="openEditor(task)">编辑</button>
          </article>
        </div>
        <div v-else class="compact-empty">{{ tasks.length ? '没有符合当前筛选的任务。' : '还没有任务，先添加第一项。' }}</div>
      </section>

      <section v-if="editorOpen" class="panel editor-panel">
        <div class="section-heading"><div><p class="eyebrow">{{ editing ? '编辑任务' : '新建任务' }}</p><h2>{{ editing?.title || '添加手动任务' }}</h2></div><button type="button" class="text-button" @click="editorOpen = false">取消</button></div>
        <form class="stack-form" @submit.prevent="saveTask">
          <div class="form-grid"><label>任务标题<input v-model.trim="form.title" required maxlength="300" /></label><label>关联课程<select v-model="form.course_id"><option value="">不关联课程</option><option v-for="course in courses" :key="course.id" :value="course.id">{{ course.name }} · {{ course.class_name || '未分班' }}</option></select></label></div>
          <label>说明<textarea v-model.trim="form.description" rows="3"></textarea></label>
          <div class="form-grid form-grid-3"><label>正式 DDL 日期<input v-model="form.due_date" type="date" /></label><label>DDL 时刻（可留空）<input v-model="form.due_time" type="time" :disabled="!form.due_date" /></label><label>优先级<select v-model="form.priority"><option value="low">低</option><option value="medium">中</option><option value="high">高</option></select></label></div>
          <div class="form-grid"><label>原始预计分钟<input v-model="form.estimated_minutes" type="number" min="1" /></label><label>当前剩余分钟<input v-model="form.remaining_minutes" type="number" min="0" /></label></div>
          <p class="form-help">只填写日期时会按原精度显示，不会补成 00:00 或 23:59。</p>
          <p v-if="formError" class="form-error" role="alert">{{ formError }}</p>
          <button type="submit" :disabled="saving">{{ saving ? '正在保存…' : '保存任务' }}</button>
        </form>
      </section>
    </template>
  </main>
</template>
