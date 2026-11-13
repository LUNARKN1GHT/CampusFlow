<script setup lang="ts">
/*
 * 任务页：维护手动任务、执行进度、必要依赖与子任务，并展示全空间的依赖检查结果。
 * 流程：筛任务 → 编辑 DDL／优先级／剩余投入 → 单独更新执行进度；依赖与子任务面板负责关系、拆分建议与创建。
 * 约束：核对状态来自候选事项，与执行进度分开维护；完成／取消会把剩余耗时归零，重开按原始估计恢复并保留历史。
 * 写操作失败只在当前面板显示错误并保留已填内容，不关闭面板，也不显示成功提示。
 */
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import { coursesApi, semestersApi, tasksApi } from '../api/business'
import { errorMessage } from '../api/client'
import type {
  Course,
  DependencyCheck,
  DueSourceType,
  Semester,
  SplitSuggestion,
  Task,
  TaskInput,
  TaskPriority,
  TaskProgress,
  TaskProgressChange,
  TaskRelations,
} from '../api/types'
import ApiFeedback from '../components/ApiFeedback.vue'
import FixedSchedule from '../components/FixedSchedule.vue'
import { priorityLabels, progressLabels } from '../constants/labels'
import { formatDateTime, formatDueDate } from '../utils/datetime'
import { parsePositiveInt } from '../utils/query'

const route = useRoute()
const tasks = ref<Task[]>([])
const courses = ref<Course[]>([])
const semesters = ref<Semester[]>([])
const loading = ref(true)
const saving = ref(false)
const error = ref('')
const formError = ref('')
const editorOpen = ref(false)
const editorPanel = ref<HTMLElement | null>(null)
const editorTrigger = ref<HTMLElement | null>(null)
const editing = ref<Task | null>(null)
const progressTask = ref<Task | null>(null)
const progressPanel = ref<HTMLElement | null>(null)
const progressTrigger = ref<HTMLElement | null>(null)
const history = ref<TaskProgressChange[]>([])
const historyLoading = ref(false)
const historyError = ref('')
let historyRequestId = 0
const progressSaving = ref(false)
const progressError = ref('')
const progressForm = reactive({ progress: 'in_progress' as TaskProgress, reason: '' })
const selectedTask = ref<Task | null>(null)
const relations = ref<TaskRelations | null>(null)
const relationsLoading = ref(false)
const relationsError = ref('')
let relationsRequestId = 0
const dependencyCheck = ref<DependencyCheck | null>(null)
const dependencyInput = ref('')
const splitSuggestion = ref<SplitSuggestion | null>(null)
let splitSuggestionRequestId = 0
let splitSourceTaskId: number | null = null
const subtaskRows = ref<Array<{ title: string; remaining_minutes: number | null }>>([])
const subtaskError = ref('')
const subtaskSaving = ref(false)
const filters = reactive({ semesterId: '', courseId: '', progress: '' })
const listNotice = ref('')
const form = reactive({
  title: '', description: '', course_id: '', due_date: '', due_time: '',
  target_date: '', due_source: 'user' as DueSourceType, due_note: '',
  priority: 'medium' as TaskPriority, estimated_minutes: '', remaining_minutes: '',
})

const courseMap = computed(() => new Map(courses.value.map((course) => [course.id, course])))
const filteredCourses = computed(() => filters.semesterId
  ? courses.value.filter((course) => course.semester_id === Number(filters.semesterId))
  : courses.value)
// 学期筛选按任务的课程归属判断；未关联课程的任务在选定学期时一律隐藏，需切回“全部学期”。
// 新建成功后也用它判断是否命中当前筛选，未命中时给出提示而不是静默隐藏。
function matchesFilters(task: Task): boolean {
  if (filters.courseId && task.course_id !== Number(filters.courseId)) return false
  if (filters.progress && task.progress !== filters.progress) return false
  if (filters.semesterId) {
    if (task.course_id === null) return false
    if (courseMap.value.get(task.course_id)?.semester_id !== Number(filters.semesterId)) return false
  }
  return true
}
const visibleTasks = computed(() => tasks.value.filter(matchesFilters))
// 调整筛选后“不在当前筛选结果中”的提示不再成立，立即清除。
watch(filters, () => {
  listNotice.value = ''
})

const dueSourceLabels: Record<DueSourceType, string> = { user: '用户补充', document: '原文规定', inferred: '系统推断' }

/** 核对界面确认后生成的正式事项由此参数直达并高亮。 */
const highlightedTaskId = computed(() => parsePositiveInt(route.query.task))

async function scrollToHighlighted(): Promise<void> {
  await nextTick()
  if (highlightedTaskId.value === null) return
  document.querySelector(`[data-task-id="${highlightedTaskId.value}"]`)?.scrollIntoView({ block: 'center' })
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    ;[tasks.value, courses.value, semesters.value] = await Promise.all([
      tasksApi.list(), coursesApi.list(), semestersApi.list(true),
    ])
    try {
      dependencyCheck.value = await tasksApi.dependencyCheck()
    } catch {
      // 依赖检查是辅助信息：读取失败时隐藏该面板，不阻塞任务列表和筛选。
      dependencyCheck.value = null
    }
  } catch (caught) {
    error.value = errorMessage(caught)
  } finally {
    loading.value = false
  }
  await scrollToHighlighted()
}

// 面板渲染在列表末尾：打开后滚动进入视口并聚焦首个表单控件，关闭时把焦点交还触发按钮。
function triggerElement(event?: MouseEvent): HTMLElement | null {
  return event?.currentTarget instanceof HTMLElement ? event.currentTarget : null
}

function focusPanel(panel: HTMLElement | null) {
  if (!panel) return
  panel.scrollIntoView({ block: 'nearest' })
  const first = panel.querySelector<HTMLElement>('input, select, textarea') ?? panel.querySelector<HTMLElement>('button')
  first?.focus()
}

function restoreFocus(trigger: HTMLElement | null) {
  if (trigger && document.contains(trigger)) trigger.focus()
}

function closeEditor() {
  editorOpen.value = false
  restoreFocus(editorTrigger.value)
  editorTrigger.value = null
}

async function openEditor(task?: Task, event?: MouseEvent) {
  editorTrigger.value = triggerElement(event)
  editing.value = task ?? null
  editorOpen.value = true
  formError.value = ''
  Object.assign(form, {
    title: task?.title ?? '', description: task?.description ?? '', course_id: task?.course_id?.toString() ?? '',
    due_date: task?.due_date ?? '', due_time: task?.due_time?.slice(0, 5) ?? '', priority: task?.priority ?? 'medium',
    target_date: task?.target_date ?? '', due_source: task?.due_source ?? 'user', due_note: task?.due_note ?? '',
    estimated_minutes: task?.estimated_minutes?.toString() ?? '', remaining_minutes: task?.remaining_minutes?.toString() ?? '',
  })
  await nextTick()
  focusPanel(editorPanel.value)
}

// 只有填写了 DDL 日期才提交时刻；仅日期任务保持原精度，不补 00:00 或 23:59。
function payload(): TaskInput {
  return {
    title: form.title,
    description: form.description || null,
    course_id: form.course_id ? Number(form.course_id) : null,
    due_date: form.due_date || null,
    due_time: form.due_date ? form.due_time || null : null,
    target_date: form.target_date || null,
    due_source: form.due_source,
    due_note: form.due_note || null,
    priority: form.priority,
    estimated_minutes: form.estimated_minutes ? Number(form.estimated_minutes) : null,
    remaining_minutes: form.remaining_minutes ? Number(form.remaining_minutes) : null,
  }
}

// 更新结果可能不在当前列表里（筛选变化或另一标签页新建）；找不到时不写入 -1 属性，改为重新读取列表并说明原因。
async function replaceTask(updated: Task) {
  const index = tasks.value.findIndex((task) => task.id === updated.id)
  if (index === -1) {
    await load()
    if (!error.value) {
      listNotice.value = '任务已保存，但不在当前筛选结果中；已重新加载任务列表，请调整筛选查看。'
    }
    return
  }
  tasks.value[index] = updated
}

// 编辑成功替换列表中的同一项，新建成功追加到列表；失败不关闭面板，避免输入丢失。
async function saveTask() {
  saving.value = true
  formError.value = ''
  try {
    const values = payload()
    if (editing.value) {
      const updated = await tasksApi.update(editing.value.id, values)
      await replaceTask(updated)
    } else {
      const created = await tasksApi.create(values)
      tasks.value.push(created)
      // 新建的任务可能不命中当前筛选，静默隐藏会让用户以为创建失败而重复创建。
      if (matchesFilters(created)) {
        listNotice.value = ''
      } else {
        listNotice.value = '任务已创建，但不在当前筛选结果中；请调整筛选查看'
      }
    }
    closeEditor()
  } catch (caught) {
    formError.value = errorMessage(caught)
  } finally {
    saving.value = false
  }
}

function closeProgress() {
  // 关闭时让进行中的读取失效，避免请求返回后写回已关闭面板的数据。
  historyRequestId += 1
  progressTask.value = null
  history.value = []
  historyError.value = ''
  historyLoading.value = false
  restoreFocus(progressTrigger.value)
  progressTrigger.value = null
}

// 读取调整历史前先清空旧数据，切换任务时不会残留上一个任务的记录。
// 请求序号用于丢弃过期响应：快速切换任务时，先发后到的慢请求不会把旧任务历史写回面板。
async function loadHistory(task: Task) {
  const requestId = ++historyRequestId
  history.value = []
  historyError.value = ''
  historyLoading.value = true
  try {
    const records = await tasksApi.progressHistory(task.id)
    if (requestId !== historyRequestId) return
    history.value = records
  } catch (caught) {
    if (requestId !== historyRequestId) return
    history.value = []
    historyError.value = errorMessage(caught)
  } finally {
    if (requestId === historyRequestId) historyLoading.value = false
  }
}

// 打开新任务时先生成面板状态再读取历史，加载中与失败都不复用上一个任务的数据。
async function openProgress(task: Task, event?: MouseEvent) {
  progressTrigger.value = triggerElement(event)
  progressTask.value = task
  progressForm.progress = task.progress
  progressForm.reason = ''
  progressError.value = ''
  await nextTick()
  focusPanel(progressPanel.value)
  await loadHistory(task)
}

function reloadHistory() {
  if (!progressTask.value) return
  void loadHistory(progressTask.value)
}

// 进度与核对状态相互独立；保存成功后重新读取历史，失败时保留当前状态和已填原因。
async function saveProgress() {
  if (!progressTask.value) return
  progressSaving.value = true
  progressError.value = ''
  try {
    const updated = await tasksApi.setProgress(
      progressTask.value.id,
      progressForm.progress,
      progressForm.reason || null,
    )
    await replaceTask(updated)
    progressTask.value = updated
    progressForm.reason = ''
    await loadHistory(updated)
  } catch (caught) {
    progressError.value = errorMessage(caught)
  } finally {
    progressSaving.value = false
  }
}

function formatChangedAt(value: string): string {
  return formatDateTime(value)
}

async function refreshDependencyCheck() {
  try {
    dependencyCheck.value = await tasksApi.dependencyCheck()
  } catch {
    dependencyCheck.value = null
  }
}

// 读取关系与依赖；重试时保留原错误提示，直到请求返回前按钮都保持忙碌态。
// 请求序号与发起任务双重校验：快速从 A 切到 B 时，先发后到的慢请求不会把 A 的关系写进 B 面板。
async function loadRelations(task: Task, keepError = false) {
  const requestId = ++relationsRequestId
  if (!keepError) relationsError.value = ''
  relationsLoading.value = true
  try {
    const result = await tasksApi.relations(task.id)
    if (requestId !== relationsRequestId || selectedTask.value?.id !== task.id) return
    relations.value = result
    relationsError.value = ''
  } catch (caught) {
    if (requestId !== relationsRequestId || selectedTask.value?.id !== task.id) return
    relationsError.value = errorMessage(caught)
  } finally {
    // 加载态只允许最新请求复位，旧请求不能提前熄灭 B 的“正在读取关系”。
    if (requestId === relationsRequestId) relationsLoading.value = false
  }
}

// 切换任务时清空上一个任务的关系与子任务编辑内容，再读取关系。
async function openRelations(task: Task) {
  selectedTask.value = task
  relations.value = null
  dependencyInput.value = ''
  splitSuggestion.value = null
  subtaskRows.value = []
  subtaskError.value = ''
  splitSourceTaskId = null
  await loadRelations(task)
}

function retryRelations() {
  if (!selectedTask.value || relationsLoading.value) return
  void loadRelations(selectedTask.value, true)
}

// 后端校验空间、自依赖与成环；成功后重新读取关系并刷新全空间依赖检查。
async function addDependency() {
  const task = selectedTask.value
  if (!task) return
  const dependsOn = parsePositiveInt(dependencyInput.value)
  if (dependsOn === null) {
    relationsError.value = '请输入有效的任务编号'
    return
  }
  relationsError.value = ''
  try {
    await tasksApi.addDependency(task.id, dependsOn)
    dependencyInput.value = ''
    await openRelations(task)
    await refreshDependencyCheck()
  } catch (caught) {
    relationsError.value = errorMessage(caught)
  }
}

async function removeDependency(dependsOn: number) {
  const task = selectedTask.value
  if (!task) return
  relationsError.value = ''
  try {
    await tasksApi.removeDependency(task.id, dependsOn)
    await openRelations(task)
    await refreshDependencyCheck()
  } catch (caught) {
    relationsError.value = errorMessage(caught)
  }
}

// 拆分建议只读且可编辑：加载后填入表单，用户可修改标题和剩余耗时后再创建。
// 与关系请求一致，用序号和任务校验丢弃过期响应，并记录来源任务供创建前核对。
async function loadSplitSuggestion() {
  const task = selectedTask.value
  if (!task) return
  const requestId = ++splitSuggestionRequestId
  subtaskError.value = ''
  try {
    const suggestion = await tasksApi.splitSuggestions(task.id)
    if (requestId !== splitSuggestionRequestId || selectedTask.value?.id !== task.id) return
    splitSuggestion.value = suggestion
    splitSourceTaskId = task.id
    subtaskRows.value = suggestion.items.map((item) => ({
      title: item.title,
      remaining_minutes: item.remaining_minutes,
    }))
  } catch (caught) {
    if (requestId !== splitSuggestionRequestId || selectedTask.value?.id !== task.id) return
    subtaskError.value = errorMessage(caught)
  }
}

function addSubtaskRow() {
  subtaskRows.value.push({ title: '', remaining_minutes: null })
}

// 空标题行会被丢弃；创建成功后重新加载任务、父任务汇总和依赖检查。
async function saveSubtasks() {
  const task = selectedTask.value
  if (!task) return
  if (splitSourceTaskId !== null && splitSourceTaskId !== task.id) {
    subtaskError.value = '拆分建议来自其他任务，请重新生成后再创建'
    return
  }
  const items = subtaskRows.value.filter((row) => row.title.trim())
  if (!items.length) {
    subtaskError.value = '至少需要一项子任务标题'
    return
  }
  subtaskSaving.value = true
  subtaskError.value = ''
  try {
    await tasksApi.createSubtasks(task.id, items)
    subtaskRows.value = []
    splitSuggestion.value = null
    await load()
    await openRelations(task)
  } catch (caught) {
    subtaskError.value = errorMessage(caught)
  } finally {
    subtaskSaving.value = false
  }
}

function editSubtask(id: number) {
  const task = tasks.value.find((item) => item.id === id)
  if (task) openEditor(task)
}

onMounted(load)
</script>

<template>
  <main class="page-content">
    <div class="page-heading page-heading-row">
      <div><p class="eyebrow">任务与计划</p><h1>任务</h1><p class="intro">手动维护 DDL、优先级与剩余投入，不改变课程原始截止信息。</p><p class="form-help">全部学期包含归档记录，归档不会隐藏待办。可按学期查看当前或历史任务；未关联课程的任务仅在全部学期中显示。</p></div>
      <button type="button" @click="openEditor(undefined, $event)">添加任务</button>
    </div>

    <ApiFeedback v-if="error" :message="error" :busy="loading" @retry="load" />
    <template v-else>
      <section class="panel filter-bar" aria-label="任务筛选">
        <label>学期<select v-model="filters.semesterId" @change="filters.courseId = ''"><option value="">全部学期</option><option v-for="semester in semesters" :key="semester.id" :value="semester.id">{{ semester.name }}{{ semester.archived ? '（已归档）' : '' }}</option></select></label>
        <label>课程<select v-model="filters.courseId"><option value="">全部课程</option><option v-for="course in filteredCourses" :key="course.id" :value="course.id">{{ course.name }} · {{ course.class_name || '未分班' }}</option></select></label>
        <label>进度<select v-model="filters.progress"><option value="">全部进度</option><option v-for="(label, value) in progressLabels" :key="value" :value="value">{{ label }}</option></select></label>
        <span class="count-badge">{{ visibleTasks.length }} 项</span>
      </section>

      <p v-if="listNotice" class="inline-notice" role="status">{{ listNotice }}</p>

      <section v-if="dependencyCheck" class="panel" aria-label="依赖检查">
        <div class="section-heading">
          <div><p class="eyebrow">依赖检查</p><h2>任务先后关系</h2></div>
          <span class="count-badge">{{ dependencyCheck.valid ? '可执行' : '存在阻塞' }}</span>
        </div>
        <p v-if="dependencyCheck.valid" class="form-help">未发现成环、范围外前置或已取消前置。</p>
        <template v-else>
          <ul class="plain-list">
            <li v-for="cycle in dependencyCheck.cycles" :key="`cycle-${cycle.task_ids.join('-')}`">{{ cycle.message }}</li>
            <li v-for="item in dependencyCheck.missing_prerequisites" :key="`missing-${item.task_id}-${item.code}`">{{ item.title }}：{{ item.message }}</li>
            <li v-for="item in dependencyCheck.cancelled_prerequisites" :key="`cancelled-${item.task_id}`">{{ item.title }}：{{ item.message }}</li>
          </ul>
          <p class="form-help">阻塞任务：{{ dependencyCheck.blocked_tasks.map((item) => item.title).join('、') || '无' }}。依赖不可满足时排期会把后续任务列为未排入。</p>
        </template>
      </section>

      <section class="panel">
        <p v-if="loading" class="muted">正在加载任务…</p>
        <div v-else-if="visibleTasks.length" class="task-list">
          <!-- 从核对页跳转时带 ?task=编号，用于滚动定位并高亮对应任务。 -->
          <article v-for="task in visibleTasks" :key="task.id" class="task-row" :class="{ 'is-highlighted': task.id === highlightedTaskId }" :data-task-id="task.id">
            <div class="priority-marker" :data-priority="task.priority"></div>
            <div class="task-main">
              <div class="task-title-line"><h3>{{ task.title }}</h3><span class="status-pill">{{ progressLabels[task.progress] }}</span><span v-if="task.overdue" class="status-pill overdue-pill">已逾期</span></div>
              <p>{{ courseMap.get(task.course_id ?? -1)?.name ?? '未关联课程' }} · {{ formatDueDate(task.due_date, task.due_time) }}</p>
              <p v-if="task.target_date">个人目标：{{ task.target_date }}（不改变正式 DDL）</p>
              <p>截止来源：{{ dueSourceLabels[task.due_source] }}{{ task.due_note ? ` · ${task.due_note}` : '' }}</p>
              <p v-if="task.source_state === 'invalidated'" class="inline-notice">{{ task.source_invalidated_note ?? '来源资料已删除，引用不可验证；个人任务仍保留。' }}</p>
            </div>
            <div class="task-meta">
              <span>优先级 {{ priorityLabels[task.priority] }}</span>
              <template v-if="task.subtask_rollup">
                <strong>{{ task.subtask_rollup.remaining_minutes }}<small> 分钟剩余（子任务汇总）</small></strong>
                <span>完成度 {{ task.subtask_rollup.percent_complete === null ? '—' : `${task.subtask_rollup.percent_complete}%` }}</span>
              </template>
              <strong v-else>{{ task.remaining_minutes ?? '—' }}<small> 分钟剩余</small></strong>
            </div>
            <div class="task-actions"><button type="button" class="button-secondary" @click="openRelations(task)">依赖与子任务</button><button type="button" class="button-secondary" @click="openProgress(task, $event)">进度</button><button type="button" class="text-button" @click="openEditor(task, $event)">编辑</button></div>
          </article>
        </div>
        <div v-else class="compact-empty">{{ tasks.length ? '没有符合当前筛选的任务。' : '还没有任务，先添加第一项。' }}</div>
      </section>

      <section v-if="editorOpen" ref="editorPanel" class="panel editor-panel">
        <div class="section-heading"><div><p class="eyebrow">{{ editing ? '编辑任务' : '新建任务' }}</p><h2>{{ editing?.title || '添加手动任务' }}</h2></div><button type="button" class="text-button" @click="closeEditor">取消</button></div>
        <form class="stack-form" @submit.prevent="saveTask">
          <div class="form-grid"><label>任务标题<input v-model.trim="form.title" required maxlength="300" /></label><label>关联课程<select v-model="form.course_id"><option value="">不关联课程</option><option v-for="course in courses" :key="course.id" :value="course.id">{{ course.name }} · {{ course.class_name || '未分班' }}</option></select></label></div>
          <label>说明<textarea v-model.trim="form.description" rows="3"></textarea></label>
          <div class="form-grid form-grid-3"><label>正式 DDL 日期<input v-model="form.due_date" type="date" /></label><label>DDL 时刻（可留空）<input v-model="form.due_time" type="time" :disabled="!form.due_date" /></label><label>优先级<select v-model="form.priority"><option value="low">低</option><option value="medium">中</option><option value="high">高</option></select></label></div>
          <div class="form-grid"><label>个人目标日期<input v-model="form.target_date" type="date" /></label><label>截止来源<select v-model="form.due_source" aria-label="截止来源"><option v-for="(label, value) in dueSourceLabels" :key="value" :value="value">{{ label }}</option></select></label></div>
          <label>截止来源说明<input v-model.trim="form.due_note" maxlength="500" placeholder="例如：用户补充，或课程大纲第 3 页；尚未核对时请注明" /></label>
          <p class="form-help">个人目标是自己的安排，调整目标不会修改正式 DDL。来源类型与说明由你录入，不代表系统已核验原文。</p>
          <div class="form-grid"><label>原始预计分钟<input v-model="form.estimated_minutes" type="number" min="1" /></label><label>当前剩余分钟<input v-model="form.remaining_minutes" type="number" min="0" /></label></div>
          <p class="form-help">只填写日期时会按原精度显示，不会补成 00:00 或 23:59。</p>
          <p v-if="formError" class="form-error" role="alert">{{ formError }}</p>
          <button type="submit" :disabled="saving">{{ saving ? '正在保存…' : '保存任务' }}</button>
        </form>
      </section>

      <section v-if="progressTask" ref="progressPanel" class="panel editor-panel">
        <div class="section-heading"><div><p class="eyebrow">执行进度</p><h2>{{ progressTask.title }}</h2></div><button type="button" class="text-button" @click="closeProgress">关闭</button></div>
        <div class="progress-layout">
          <form class="stack-form" @submit.prevent="saveProgress">
            <label>新状态<select v-model="progressForm.progress"><option v-for="(label, value) in progressLabels" :key="value" :value="value">{{ label }}</option></select></label>
            <label>调整原因<textarea v-model.trim="progressForm.reason" rows="3" maxlength="500" placeholder="例如：依赖的实验数据尚未提供"></textarea></label>
            <p class="form-help">完成或取消会将剩余耗时记为 0；重新打开时按原始估计恢复，历史记录不会删除。</p>
            <p v-if="progressError" class="form-error" role="alert">{{ progressError }}</p>
            <!-- 与当前状态相同时禁用提交，避免生成没有变化的历史记录。 -->
            <button type="submit" :disabled="progressSaving || progressForm.progress === progressTask.progress">{{ progressSaving ? '正在保存…' : '更新进度' }}</button>
          </form>
          <div>
            <h3 class="subheading">调整历史</h3>
            <p v-if="historyLoading" class="muted">正在读取调整历史…</p>
            <template v-else-if="historyError">
              <div class="compact-empty">{{ historyError }}</div>
              <div class="page-actions"><button type="button" class="text-button" @click="reloadHistory">重新加载历史</button></div>
            </template>
            <ol v-else-if="history.length" class="history-list">
              <li v-for="change in history" :key="change.id"><div><strong>{{ progressLabels[change.from_progress] }} → {{ progressLabels[change.to_progress] }}</strong><time>{{ formatChangedAt(change.changed_at) }}</time></div><p>{{ change.reason || '未填写原因' }}</p><small>剩余耗时：{{ change.previous_remaining_minutes ?? '—' }} → {{ change.new_remaining_minutes ?? '—' }} 分钟</small></li>
            </ol>
            <div v-else class="compact-empty">还没有进度调整记录。</div>
          </div>
        </div>
      </section>
      <section v-if="selectedTask" class="panel editor-panel" aria-label="依赖与子任务">
        <div class="section-heading">
          <div><p class="eyebrow">依赖与子任务</p><h2>{{ selectedTask.title }}</h2></div>
          <button type="button" class="text-button" @click="selectedTask = null">关闭</button>
        </div>
        <ApiFeedback v-if="relationsError" :message="relationsError" title="关系操作未完成" :busy="relationsLoading" @retry="retryRelations" />
        <p v-if="relationsLoading" class="muted">正在读取关系…</p>
        <template v-else-if="relations">
          <p class="form-help">
            子任务 {{ relations.rollup.subtask_count }} 项 · 预计 {{ relations.rollup.estimated_minutes }} 分钟 ·
            剩余 {{ relations.rollup.remaining_minutes }} 分钟 ·
            完成度 {{ relations.rollup.percent_complete === null ? '—' : `${relations.rollup.percent_complete}%` }}。
            父任务只做汇总，排期只安排子任务，不会重复计入耗时。
          </p>
          <div class="form-grid">
            <div>
              <h3 class="subheading">前置任务</h3>
              <ul v-if="relations.prerequisites.length" class="plain-list">
                <li v-for="item in relations.prerequisites" :key="item.id">
                  {{ item.title }}
                  <button type="button" class="text-button" @click="removeDependency(item.id)">删除依赖</button>
                </li>
              </ul>
              <p v-else class="muted">没有前置任务。</p>
              <div class="page-actions">
                <input v-model="dependencyInput" type="number" min="1" placeholder="前置任务编号" aria-label="前置任务编号" />
                <button type="button" class="button-secondary" @click="addDependency">添加依赖</button>
              </div>
              <p v-if="relations.dependents.length" class="form-help">
                后续任务：{{ relations.dependents.map((item) => item.title).join('、') }}
              </p>
            </div>
            <div>
              <h3 class="subheading">子任务</h3>
              <ul v-if="relations.subtasks.length" class="plain-list">
                <li v-for="item in relations.subtasks" :key="item.id">
                  {{ item.title }} · {{ progressLabels[item.progress] ?? item.progress }}
                  <button type="button" class="text-button" @click="editSubtask(item.id)">编辑</button>
                </li>
              </ul>
              <p v-else class="muted">还没有子任务。</p>
            </div>
          </div>

          <div class="section-heading">
            <div><p class="eyebrow">拆分建议</p><h3 class="subheading">系统拆分只是可编辑建议</h3></div>
            <div class="page-actions">
              <button type="button" class="button-secondary" @click="loadSplitSuggestion">生成拆分建议</button>
              <button type="button" class="text-button" @click="addSubtaskRow">手动添加一行</button>
            </div>
          </div>
          <p v-if="splitSuggestion" class="inline-notice">{{ splitSuggestion.note }}</p>
          <div v-if="subtaskRows.length" class="stack-form">
            <div v-for="(row, index) in subtaskRows" :key="index" class="form-grid">
              <label>子任务标题<input v-model.trim="row.title" maxlength="300" /></label>
              <label>剩余分钟<input v-model.number="row.remaining_minutes" type="number" min="0" /></label>
            </div>
            <p v-if="subtaskError" class="form-error" role="alert">{{ subtaskError }}</p>
            <button type="button" :disabled="subtaskSaving" @click="saveSubtasks">
              {{ subtaskSaving ? '创建中…' : '按以上内容创建子任务' }}
            </button>
          </div>
        </template>
      </section>

      <FixedSchedule />
    </template>
  </main>
</template>
