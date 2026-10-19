<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'

import { coursesApi, tasksApi } from '../api/business'
import { ApiError, errorMessage } from '../api/client'
import { exportsApi } from '../api/exports'
import type {
  ExportCounts,
  ExportFormat,
  ExportItem,
  ExportMode,
  ExportPreview,
  ExportRequest,
} from '../api/exports'
import { plansApi } from '../api/plans'
import type { PlanDraft } from '../api/plans'
import type { Course, Task, TaskProgress } from '../api/types'
import ApiFeedback from '../components/ApiFeedback.vue'

const mode = ref<ExportMode>('tasks')
const format = ref<ExportFormat>('csv')
const tasks = ref<Task[]>([])
const courses = ref<Course[]>([])
const acceptedPlans = ref<PlanDraft[]>([])
const selectedTaskIds = ref<number[]>([])
const selectedPlanId = ref<number | ''>('')
const taskSearch = ref('')
const includeSourceHints = ref(false)
const includeFixedEvents = ref(false)

const loading = ref(true)
const loadError = ref('')
const previewing = ref(false)
const downloading = ref(false)
const preview = ref<ExportPreview | null>(null)
const fingerprint = ref('')
const formError = ref('')
const actionError = ref('')
const downloadNotice = ref('')

const progressLabels: Record<TaskProgress, string> = {
  not_started: '未开始',
  in_progress: '进行中',
  blocked: '受阻',
  done: '已完成',
  cancelled: '已取消',
}

/** 预览与导出支持的警告／排除代码；未知代码仍原样显示，不隐藏。 */
const noticeLabels: Record<string, string> = {
  candidate_unconfirmed: '候选未确认',
  invalid_source_claim: '来源不可核对',
  raw_material_text: '原文内容',
  cancelled_task: '任务已取消',
  buffer_block: '提交缓冲',
  orphan_block: '孤立时间块',
  unscheduled_task: '未排入任务',
  no_due_date: '没有截止日期',
  fixed_event_disabled: '未勾选固定日程',
  fixed_event_out_of_range: '固定日程超出范围',
  date_only_ddl: '仅日期截止',
  missing_due_date: '缺少截止日期',
  source_invalidated: '来源失效',
  empty_export: '结果为空',
  timezone_unreadable: '时区不可读',
  no_accepted_plan: '没有已接受计划',
  stale_export_scope: '范围已变化',
}

const courseMap = computed(() => new Map(courses.value.map((course) => [course.id, course])))
const selectedSet = computed(() => new Set(selectedTaskIds.value))
const selectedPlan = computed(() => acceptedPlans.value.find((plan) => plan.id === selectedPlanId.value) ?? null)
const fixedEventsAvailable = computed(() => mode.value === 'plan' && format.value === 'ics')

const filteredTasks = computed(() => {
  const keyword = taskSearch.value.trim().toLowerCase()
  if (!keyword) return tasks.value
  return tasks.value.filter((task) => {
    const courseName = courseMap.value.get(task.course_id ?? -1)?.name ?? ''
    return `${task.title} ${courseName}`.toLowerCase().includes(keyword)
  })
})

const outputCount = computed(() => {
  const current = preview.value
  if (!current) return 0
  return current.format === 'ics' ? current.event_count ?? current.row_count : current.row_count
})

const outputLabel = computed(() => (preview.value?.format === 'ics' ? '日历事件' : '清单行'))

const dateRangeText = computed(() => {
  const range = preview.value?.date_range
  if (!range || !range.start || !range.end) return '没有带日期的条目'
  return `${range.start} 至 ${range.end}${range.has_date_only ? '（含仅日期截止）' : ''}`
})

const countEntries = computed(() => {
  const counts: ExportCounts | undefined = preview.value?.counts
  if (!counts) return []
  return [
    { key: 'date_only', label: '仅日期截止', value: counts.date_only, attention: false },
    { key: 'timed', label: '带时刻', value: counts.timed, attention: false },
    { key: 'no_due', label: '没有截止', value: counts.no_due, attention: counts.no_due > 0 },
    { key: 'scheduled', label: '已排入', value: counts.scheduled, attention: false },
    { key: 'unscheduled', label: '未排入', value: counts.unscheduled, attention: counts.unscheduled > 0 },
    { key: 'source_invalidated', label: '来源失效', value: counts.source_invalidated, attention: counts.source_invalidated > 0 },
    { key: 'cancelled', label: '已取消', value: counts.cancelled, attention: counts.cancelled > 0 },
  ]
})

const scopeText = computed(() => {
  const scope = preview.value?.scope
  if (!scope) return ''
  if (preview.value?.mode === 'plan') {
    return `计划 #${scope.plan_id} · 版本 v${scope.plan_version ?? '—'} · 学期 ${scope.semester_id ?? '未指定'} · ${scope.range_start ?? '—'} 至 ${scope.range_end ?? '—'}`
  }
  const ids = scope.task_ids.map((id) => `#${id}`)
  return `任务共 ${ids.length} 项：${ids.join('、') || '无'}`
})

const canDownload = computed(() => Boolean(preview.value?.can_download && fingerprint.value))

const downloadDisabledReason = computed(() => {
  if (!preview.value) return '请先预览；预览成功后才能下载。'
  if (!preview.value.can_download) {
    const reason = preview.value.warnings.find((notice) =>
      ['empty_export', 'no_accepted_plan', 'timezone_unreadable'].includes(notice.code),
    )
    return reason ? `当前预览不可下载：${reason.message}` : '当前预览没有可导出的条目，下载已禁用。'
  }
  if (!fingerprint.value) return '导出范围已变化，请重新预览。'
  return ''
})

function taskDueText(task: Task): string {
  if (!task.due_date) return '未设置 DDL'
  return task.due_time ? `${task.due_date} ${task.due_time.slice(0, 5)}` : `${task.due_date}（仅日期）`
}

function itemDueText(item: ExportItem): string {
  if (!item.due_date) return '未设置 DDL'
  return item.due_time ? `${item.due_date} ${item.due_time.slice(0, 5)}` : `${item.due_date}（仅日期）`
}

function noticeLabel(code: string): string {
  return noticeLabels[code] ?? code
}

function isSelected(id: number): boolean {
  return selectedSet.value.has(id)
}

function toggleTask(id: number): void {
  selectedTaskIds.value = selectedTaskIds.value.includes(id)
    ? selectedTaskIds.value.filter((taskId) => taskId !== id)
    : [...selectedTaskIds.value, id].sort((left, right) => left - right)
}

function clearSelection(): void {
  selectedTaskIds.value = []
}

/** 任何范围或选项变化都使旧预览失效，避免下载超出当前展示的内容。 */
function resetPreview(): void {
  preview.value = null
  fingerprint.value = ''
  downloadNotice.value = ''
  actionError.value = ''
  formError.value = ''
}

function requestBody(withFingerprint: boolean): ExportRequest {
  return {
    mode: mode.value,
    format: format.value,
    task_ids: mode.value === 'tasks' ? [...selectedTaskIds.value] : null,
    plan_id: mode.value === 'plan' && selectedPlanId.value !== '' ? Number(selectedPlanId.value) : null,
    options: {
      include_source_hints: includeSourceHints.value,
      include_fixed_events: fixedEventsAvailable.value && includeFixedEvents.value,
    },
    scope_fingerprint: withFingerprint ? fingerprint.value : null,
  }
}

function validateScope(): string {
  if (mode.value === 'tasks' && selectedTaskIds.value.length === 0) {
    return '请显式勾选至少一项任务；系统不提供整空间导出。'
  }
  if (mode.value === 'plan' && selectedPlanId.value === '') {
    return '请选择一份已接受计划；草案不能被导出。'
  }
  return ''
}

async function runPreview(): Promise<void> {
  formError.value = ''
  actionError.value = ''
  downloadNotice.value = ''
  const problem = validateScope()
  if (problem) {
    formError.value = problem
    return
  }
  previewing.value = true
  try {
    const result = await exportsApi.preview(requestBody(false))
    preview.value = result
    fingerprint.value = result.scope_fingerprint
  } catch (caught) {
    actionError.value = errorMessage(caught)
  } finally {
    previewing.value = false
  }
}

function fallbackFilename(): string {
  const now = new Date()
  const stamp = [
    now.getFullYear(),
    String(now.getMonth() + 1).padStart(2, '0'),
    String(now.getDate()).padStart(2, '0'),
  ].join('')
  return `campusflow-${mode.value}-${stamp}.${format.value}`
}

function saveFile(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}

async function download(): Promise<void> {
  formError.value = ''
  actionError.value = ''
  downloadNotice.value = ''
  if (!preview.value || !fingerprint.value) {
    formError.value = '请先预览导出范围，确认后再下载。'
    return
  }
  if (!preview.value.can_download) {
    formError.value = downloadDisabledReason.value
    return
  }
  downloading.value = true
  try {
    const file = await exportsApi.download(requestBody(true))
    const filename = file.filename ?? fallbackFilename()
    saveFile(file.blob, filename)
    downloadNotice.value = `已开始下载 ${filename}。文件只保存到本机，服务端不保留导出文件。`
  } catch (caught) {
    if (caught instanceof ApiError && caught.status === 409) {
      preview.value = null
      fingerprint.value = ''
      actionError.value = '范围已变化，请重新预览。'
    } else {
      actionError.value = errorMessage(caught)
    }
  } finally {
    downloading.value = false
  }
}

async function load(): Promise<void> {
  loading.value = true
  loadError.value = ''
  try {
    const [taskItems, courseItems, drafts] = await Promise.all([
      tasksApi.list(),
      coursesApi.list(),
      plansApi.list(),
    ])
    tasks.value = taskItems
    courses.value = courseItems
    acceptedPlans.value = drafts
      .filter((draft) => draft.status === 'accepted')
      .sort((left, right) => right.range_start.localeCompare(left.range_start))
  } catch (caught) {
    loadError.value = errorMessage(caught)
  } finally {
    loading.value = false
  }
}

watch(
  [mode, format, selectedPlanId, includeSourceHints, includeFixedEvents, selectedTaskIds],
  resetPreview,
)
watch(fixedEventsAvailable, (available) => {
  if (!available) includeFixedEvents.value = false
})
watch(mode, (value) => {
  if (value === 'plan' && format.value === 'csv') format.value = 'ics'
})

onMounted(load)
</script>

<template>
  <main class="page-content">
    <div class="page-heading">
      <p class="eyebrow">清单与日历</p>
      <h1>导出</h1>
      <p class="intro">把显式选择的任务或当前已接受计划导出为任务清单或日历文件草案。</p>
      <p class="form-help">
        导出只生成本机文件：不会写入外部日历、不会发送消息，也不会自动同步。文件不包含任务描述、截止备注、资料原文、片段或引用摘录；来源提示最多到资料标题、版本号和定位。
      </p>
    </div>

    <ApiFeedback v-if="loadError" :message="loadError" :busy="loading" @retry="load" />
    <div v-else class="export-stack">
      <section class="panel stack-form" aria-label="导出范围">
        <div class="section-heading">
          <div><p class="eyebrow">第一步</p><h2>选择范围</h2></div>
          <span class="count-badge">
            {{ mode === 'tasks' ? `已选 ${selectedTaskIds.length} 项` : (selectedPlan ? `计划 v${selectedPlan.version}` : '未选择计划') }}
          </span>
        </div>

        <div class="segmented" role="group" aria-label="导出模式">
          <button type="button" class="segmented-item" :class="{ 'is-active': mode === 'tasks' }" :aria-pressed="mode === 'tasks'" @click="mode = 'tasks'">所选任务</button>
          <button type="button" class="segmented-item" :class="{ 'is-active': mode === 'plan' }" :aria-pressed="mode === 'plan'" @click="mode = 'plan'">已接受计划</button>
        </div>

        <template v-if="mode === 'tasks'">
          <div class="export-filter">
            <label>搜索任务<input v-model="taskSearch" type="search" placeholder="按任务标题或课程名查找" /></label>
            <span class="count-badge">{{ filteredTasks.length }} / {{ tasks.length }} 项</span>
          </div>
          <p class="form-help">搜索只用于查找，筛选结果不会自动成为导出范围；请逐项勾选。系统不提供整空间导出。</p>

          <p v-if="loading" class="muted">正在读取任务…</p>
          <div v-else-if="filteredTasks.length" class="export-select-list">
            <label
              v-for="task in filteredTasks"
              :key="task.id"
              class="export-select-row"
              :class="{ 'is-selected': isSelected(task.id) }"
            >
              <input type="checkbox" :checked="isSelected(task.id)" @change="toggleTask(task.id)" />
              <span class="export-select-main">
                <span class="task-title-line">
                  <strong>{{ task.title }}</strong>
                  <span class="status-pill">{{ progressLabels[task.progress] }}</span>
                  <span v-if="task.overdue" class="status-pill overdue-pill">已逾期</span>
                </span>
                <small>{{ courseMap.get(task.course_id ?? -1)?.name ?? '未关联课程' }} · {{ taskDueText(task) }}</small>
                <small v-if="task.source_state === 'invalidated'">来源资料已失效；任务仍可导出，但来源提示会被省略。</small>
              </span>
            </label>
          </div>
          <div v-else class="compact-empty">
            {{ tasks.length ? '没有符合搜索条件的任务。' : '还没有任务，请先在任务页添加。' }}
          </div>

          <div v-if="selectedTaskIds.length" class="export-action-row">
            <button type="button" class="text-button" @click="clearSelection">清空选择</button>
            <span class="form-help">已选任务编号：{{ selectedTaskIds.map((id) => `#${id}`).join('、') }}</span>
          </div>
        </template>

        <template v-else>
          <label class="stack-field">已接受计划
            <select v-model="selectedPlanId" :disabled="!acceptedPlans.length">
              <option value="">请选择一份已接受计划</option>
              <option v-for="plan in acceptedPlans" :key="plan.id" :value="plan.id">
                v{{ plan.version }} · {{ plan.range_start }} 至 {{ plan.range_end }} · 计划 #{{ plan.id }}
              </option>
            </select>
          </label>
          <p v-if="!acceptedPlans.length" class="inline-notice">
            还没有已接受计划。请先在「学习计划」接受草案；系统不会自动接受草案，也不会把草案当作当前计划导出。
          </p>
          <p v-else class="form-help">
            计划模式导出该已接受计划中的任务时间块；未排入任务在清单中保留，日历文件无法定位时会排除并警告。导出不会修改计划、任务 DDL 或锁定状态。
          </p>
          <div v-if="selectedPlan" class="export-scope">
            <p><strong>计划范围</strong> {{ selectedPlan.range_start }} 至 {{ selectedPlan.range_end }} · 版本 v{{ selectedPlan.version }} · 时区 {{ selectedPlan.timezone }}</p>
            <p><strong>计划标识</strong> #{{ selectedPlan.id }}<span v-if="selectedPlan.semester_id"> · 学期 {{ selectedPlan.semester_id }}</span></p>
          </div>
        </template>
      </section>

      <section class="panel stack-form" aria-label="格式与选项">
        <div class="section-heading">
          <div><p class="eyebrow">第二步</p><h2>格式与选项</h2></div>
          <span class="count-badge">{{ format === 'csv' ? 'CSV 任务清单' : 'ICS 日历草案' }}</span>
        </div>

        <div class="segmented" role="group" aria-label="导出格式">
          <button type="button" class="segmented-item" :class="{ 'is-active': format === 'csv' }" :aria-pressed="format === 'csv'" :disabled="mode === 'plan'" @click="format = 'csv'">CSV 任务清单</button>
          <button type="button" class="segmented-item" :class="{ 'is-active': format === 'ics' }" :aria-pressed="format === 'ics'" @click="format = 'ics'">ICS 日历草案</button>
        </div>
        <p v-if="mode === 'plan'" class="form-help">计划模式当前只提供 ICS 日历草案；计划清单 CSV 尚未提供，不会生成空文件。</p>

        <label class="switch-field">
          <input v-model="includeSourceHints" type="checkbox" />
          包含来源提示（只含资料标题、版本号和定位，不含原文摘录）
        </label>
        <p class="form-help">来源提示默认关闭。来源已删除、归档或无法定位时，整条提示省略，不会输出旧标题或脱离原文的定位。</p>

        <label class="switch-field">
          <input v-model="includeFixedEvents" type="checkbox" :disabled="!fixedEventsAvailable" />
          同时导出固定日程（仅计划模式的 ICS）
        </label>
        <p class="form-help">
          固定日程默认不导出；只有计划模式的日历文件可以勾选，且会逐条出现在预览中。固定日程的地点可能包含房间信息。
          <span v-if="mode === 'plan' && format === 'csv'">CSV 清单不包含固定日程。</span>
        </p>

        <p v-if="format === 'ics'" class="inline-notice">
          导出的日历文件只是草案，尚未写入外部日历；不会发送消息或自动同步。只有日期的截止按全天导出，不补具体时刻。
        </p>
        <p v-if="mode === 'plan' && format === 'ics'" class="form-help">
          接受新草案后计划块标识可能变化，重复导入可能在日历中产生重复事件；建议先在日历客户端删除旧计划事件。
        </p>
      </section>

      <section class="panel stack-form" aria-label="预览与下载">
        <div class="section-heading">
          <div><p class="eyebrow">第三步</p><h2>预览与下载</h2></div>
          <span v-if="preview" class="count-badge">{{ preview.mode === 'tasks' ? '任务清单' : '已接受计划' }}</span>
        </div>
        <p class="form-help">必须先预览再下载；下载会携带预览生成的范围指纹。范围、格式、任务勾选或选项变化后，旧预览立即失效，需要重新预览。</p>

        <div class="export-action-row">
          <button type="button" :disabled="previewing" @click="runPreview">{{ previewing ? '预览中…' : '预览' }}</button>
          <button type="button" class="button-secondary" :disabled="!canDownload || downloading" @click="download">
            {{ downloading ? '下载中…' : '下载' }}
          </button>
        </div>
        <p v-if="formError" class="form-error" role="alert">{{ formError }}</p>
        <p v-if="actionError" class="form-error" role="alert">{{ actionError }}</p>
        <p v-if="downloadNotice" class="success-message" role="status">{{ downloadNotice }}</p>
        <p v-if="!canDownload && !formError && !actionError" class="form-help">{{ downloadDisabledReason }}</p>

        <template v-if="preview">
          <div class="export-stat-grid">
            <div class="summary-chip"><strong>{{ preview.item_count }}</strong><span>源对象</span></div>
            <div class="summary-chip"><strong>{{ outputCount }}</strong><span>{{ outputLabel }}</span></div>
            <div class="summary-chip"><strong class="export-stat-text">{{ dateRangeText }}</strong><span>日期范围</span></div>
            <div class="summary-chip"><strong class="export-stat-text">{{ preview.timezone }}</strong><span>时区</span></div>
          </div>

          <div class="export-count-grid">
            <div v-for="entry in countEntries" :key="entry.key" class="export-count" :class="{ 'is-attention': entry.attention }">
              <strong>{{ entry.value }}</strong>
              <span>{{ entry.label }}</span>
            </div>
          </div>

          <div class="export-scope">
            <p><strong>范围</strong> {{ scopeText }}</p>
            <p><strong>格式</strong> {{ preview.format === 'csv' ? 'CSV（UTF-8 带 BOM）' : 'ICS 日历草案' }} · <strong>时区</strong> {{ preview.timezone }}</p>
          </div>

          <div>
            <h3 class="subheading">排除项</h3>
            <ul v-if="preview.excluded.length" class="export-notice-list">
              <li v-for="notice in preview.excluded" :key="`excluded-${notice.code}`">
                <span class="reason-chip">{{ noticeLabel(notice.code) }}</span>
                <span>{{ notice.message }}</span>
                <span v-if="notice.count > 0" class="count-badge">{{ notice.count }} 项</span>
              </li>
            </ul>
            <p v-else class="export-quiet">本次预览没有排除项。</p>
          </div>

          <div>
            <h3 class="subheading">警告</h3>
            <ul v-if="preview.warnings.length" class="export-notice-list">
              <li v-for="notice in preview.warnings" :key="`warning-${notice.code}`">
                <span class="reason-chip">{{ noticeLabel(notice.code) }}</span>
                <span>{{ notice.message }}</span>
                <span v-if="notice.count > 0" class="count-badge">{{ notice.count }} 项</span>
              </li>
            </ul>
            <p v-else class="export-quiet">本次预览没有额外警告，但仍请核对条目与日期。</p>
          </div>

          <div>
            <h3 class="subheading">预览条目</h3>
            <p class="form-help">标题按存储内容展示；下载内容以本次预览为准。来源失效的任务仍保留，但来源提示已省略。</p>
            <ul v-if="preview.items.length" class="export-item-list">
              <li v-for="item in preview.items" :key="item.task_id" class="export-item" :class="{ 'is-excluded': !item.included }">
                <div class="task-title-line">
                  <strong>{{ item.title }}</strong>
                  <span v-if="!item.included" class="status-pill failed-pill">已排除</span>
                  <span v-if="item.source_state === 'invalidated'" class="status-pill stale-pill">来源失效</span>
                </div>
                <p>{{ item.course_name ?? '未关联课程' }} · {{ itemDueText(item) }}</p>
                <p v-if="!item.included && item.exclusion_reason">排除原因：{{ noticeLabel(item.exclusion_reason) }}</p>
                <p v-if="item.source_title">来源提示：{{ item.source_title }} · v{{ item.source_version_no ?? '—' }} · {{ item.source_locator ?? '未提供定位' }}</p>
              </li>
            </ul>
            <p v-else class="export-quiet">预览没有返回任何条目。</p>
          </div>

          <p v-if="preview.format === 'ics'" class="inline-notice">
            日历文件只是草案，尚未写入外部日历；不会发送消息或自动同步。导入日历客户端后请自行核对条目。
          </p>
        </template>
        <div v-else class="compact-empty">还没有预览。选择范围与格式后点击“预览”，确认条目和警告后再下载。</div>
      </section>
    </div>
  </main>
</template>
