<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'

import { coursesApi, semestersApi } from '../api/business'
import { errorMessage } from '../api/client'
import { jobsApi, materialsApi, type MaterialFilters } from '../api/library'
import type {
  Course,
  Material,
  MaterialSourceType,
  MaterialStatus,
  MaterialUploadItem,
  ProcessingJob,
  Semester,
} from '../api/types'
import ApiFeedback from '../components/ApiFeedback.vue'
import MaterialSource from '../components/MaterialSource.vue'

const POLL_INTERVAL_MS = 3000
// 处理中状态的自动刷新上限：超过后停止轮询，改为手动刷新，避免后台长时间不推进时空转。
const POLL_MAX_MS = 10 * 60 * 1000
const JOB_STORAGE_KEY = 'campusflow.library.jobByMaterial'

const materials = ref<Material[]>([])
const semesters = ref<Semester[]>([])
const courses = ref<Course[]>([])
const loading = ref(true)
const error = ref('')

const importOpen = ref(false)
const importMode = ref<'text' | 'files'>('text')
const importing = ref(false)
const importError = ref('')
const successMessage = ref('')
const uploadResults = ref<MaterialUploadItem[] | null>(null)

const textForm = reactive({
  title: '', content: '', publisher: '', published_at: '', semester_id: '', course_id: '', class_name: '',
})
const uploadForm = reactive({ semester_id: '', course_id: '', class_name: '', allow_duplicate: false })
const selectedFiles = ref<File[]>([])
const fileInput = ref<HTMLInputElement | null>(null)

const filters = reactive({ semesterId: '', courseId: '', sourceType: '', status: '', includeArchived: false })

/** 资料 → 最近一次解析作业的映射。上传接口只返回一次 job_id，刷新后需要它才能查询范围与重试，因此本地留存。 */
const jobByMaterial = reactive<Record<number, number>>({})
const jobs = reactive<Record<number, ProcessingJob>>({})
const retryingId = ref<number | null>(null)
const sourceMaterial = ref<Material | null>(null)

const pollPaused = ref(false)
let pollTimer: number | null = null
let pollStartedAt: number | null = null

const sourceTypeLabels: Record<MaterialSourceType, string> = {
  pdf: 'PDF', image: '图片', text: '粘贴文本', word: 'Word', web: '网页',
}
const statusLabels: Record<MaterialStatus, string> = {
  pending: '待处理', processing: '处理中', done: '完成', partial: '部分成功', failed: '失败',
}
const semesterMap = computed(() => new Map(semesters.value.map((item) => [item.id, item])))
const courseMap = computed(() => new Map(courses.value.map((item) => [item.id, item])))
const filteredCourses = computed(() => filters.semesterId
  ? courses.value.filter((course) => course.semester_id === Number(filters.semesterId))
  : courses.value)
const uploadCourses = computed(() => uploadForm.semester_id
  ? courses.value.filter((course) => course.semester_id === Number(uploadForm.semester_id))
  : courses.value)
const textCourses = computed(() => textForm.semester_id
  ? courses.value.filter((course) => course.semester_id === Number(textForm.semester_id))
  : courses.value)
const visibleMaterials = computed(() => materials.value)

function jobFor(material: Material): ProcessingJob | null {
  const jobId = jobByMaterial[material.id]
  return jobId ? jobs[jobId] ?? null : null
}

/** 仅当存在尚未进入终态的处理作业时才算“处理中”，避免对同步导入的资料无限轮询。 */
function hasActiveJob(material: Material): boolean {
  const jobId = jobByMaterial[material.id]
  return jobId ? !(jobs[jobId]?.is_terminal ?? false) : false
}

function currentFilters(): MaterialFilters {
  return {
    semesterId: filters.semesterId ? Number(filters.semesterId) : null,
    courseId: filters.courseId ? Number(filters.courseId) : null,
    sourceType: (filters.sourceType || null) as MaterialSourceType | null,
    status: (filters.status || null) as MaterialStatus | null,
    includeArchived: filters.includeArchived,
  }
}

function loadJobMap() {
  try {
    const raw = window.localStorage.getItem(JOB_STORAGE_KEY)
    if (raw) Object.entries(JSON.parse(raw) as Record<string, number>).forEach(([id, jobId]) => {
      jobByMaterial[Number(id)] = jobId
    })
  } catch {
    // 本地映射损坏时忽略，重试入口会在重新上传后恢复。
  }
}

function persistJobMap() {
  try {
    window.localStorage.setItem(JOB_STORAGE_KEY, JSON.stringify(jobByMaterial))
  } catch {
    // 隐私模式下无法写入时不影响本次会话内的查询与重试。
  }
}

function scopeText(material: Material): string {
  const parts: string[] = []
  const semester = material.semester_id ? semesterMap.value.get(material.semester_id) : undefined
  const course = material.course_id ? courseMap.value.get(material.course_id) : undefined
  parts.push(semester?.name ?? '未指定学期')
  parts.push(course ? `${course.name}${material.class_name ? ` · ${material.class_name}` : ''}` : material.class_name || '未关联课程')
  return parts.join(' · ')
}

function formatDateTime(value: string): string {
  return new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value))
}

async function ensureJob(material: Material) {
  const jobId = jobByMaterial[material.id]
  if (!jobId) return
  const cached = jobs[jobId]
  if (cached && cached.is_terminal && cached.status !== 'partial') return
  try {
    jobs[jobId] = await jobsApi.get(jobId)
  } catch {
    // 作业查询失败不阻断列表；下一次轮询或重试会再拉取。
  }
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [materialList, semesterList, courseList] = await Promise.all([
      materialsApi.list(currentFilters()), semestersApi.list(true), coursesApi.list(),
    ])
    materials.value = materialList
    semesters.value = semesterList
    courses.value = courseList
    await Promise.all(materialList.map(ensureJob))
  } catch (caught) {
    error.value = errorMessage(caught)
  } finally {
    loading.value = false
    syncPolling()
  }
}

async function refresh() {
  try {
    const materialList = await materialsApi.list(currentFilters())
    materials.value = materialList
    await Promise.all(materialList.map(ensureJob))
  } catch {
    // 轮询中的偶发失败保留上一次结果，不打断用户。
  } finally {
    syncPolling()
  }
}

function syncPolling() {
  // 有未完成的作业，或有资料处于“处理中”时保持轮询；同步导入（无作业）不轮询。
  const active = materials.value.some((material) => hasActiveJob(material) || material.status === 'processing')
  if (!active) {
    stopPolling()
    return
  }
  if (pollStartedAt === null) pollStartedAt = Date.now()
  if (Date.now() - pollStartedAt > POLL_MAX_MS) {
    stopPolling()
    pollPaused.value = true
    return
  }
  if (pollTimer === null) pollTimer = window.setInterval(refresh, POLL_INTERVAL_MS)
}

function stopPolling() {
  if (pollTimer !== null) {
    window.clearInterval(pollTimer)
    pollTimer = null
  }
  pollStartedAt = null
}

async function manualRefresh() {
  pollPaused.value = false
  await refresh()
}

async function retry(material: Material) {
  const jobId = jobByMaterial[material.id]
  retryingId.value = material.id
  error.value = ''
  try {
    if (!jobId) {
      error.value = '这份资料的上传作业不在本次会话记录里，暂时无法重试；可重新导入同一文件。'
      return
    }
    jobs[jobId] = await jobsApi.retry(jobId)
    await refresh()
  } catch (caught) {
    error.value = errorMessage(caught)
  } finally {
    retryingId.value = null
  }
}

function onFilesChosen(event: Event) {
  const input = event.target as HTMLInputElement
  selectedFiles.value = Array.from(input.files ?? [])
}

async function submitUpload() {
  if (!selectedFiles.value.length) {
    importError.value = '请先选择要导入的 PDF 或图片文件。'
    return
  }
  importing.value = true
  importError.value = ''
  successMessage.value = ''
  try {
    const result = await materialsApi.upload(selectedFiles.value, {
      semester_id: uploadForm.semester_id ? Number(uploadForm.semester_id) : null,
      course_id: uploadForm.course_id ? Number(uploadForm.course_id) : null,
      class_name: uploadForm.class_name || null,
      allow_duplicate: uploadForm.allow_duplicate,
    })
    uploadResults.value = result.results
    result.results.forEach((item) => {
      if (item.material && item.job_id) {
        jobByMaterial[item.material.id] = item.job_id
      }
    })
    persistJobMap()
    selectedFiles.value = []
    if (fileInput.value) fileInput.value.value = ''
    await refresh()
  } catch (caught) {
    importError.value = errorMessage(caught)
  } finally {
    importing.value = false
  }
}

async function submitText() {
  if (!textForm.title.trim() || !textForm.content.trim()) {
    importError.value = '标题与正文都不能为空。'
    return
  }
  importing.value = true
  importError.value = ''
  successMessage.value = ''
  uploadResults.value = null
  try {
    const result = await materialsApi.importText({
      title: textForm.title.trim(),
      content: textForm.content,
      publisher: textForm.publisher.trim() || null,
      published_at: textForm.published_at || null,
      semester_id: textForm.semester_id ? Number(textForm.semester_id) : null,
      course_id: textForm.course_id ? Number(textForm.course_id) : null,
      class_name: textForm.class_name.trim() || null,
    })
    successMessage.value = `已导入「${result.material.title}」，切分 ${result.chunks.length} 个段落。`
    Object.assign(textForm, { title: '', content: '', publisher: '', published_at: '', class_name: '' })
    await refresh()
  } catch (caught) {
    importError.value = errorMessage(caught)
  } finally {
    importing.value = false
  }
}

function switchImportMode(mode: 'text' | 'files') {
  importMode.value = mode
  importError.value = ''
  successMessage.value = ''
  uploadResults.value = null
}

onMounted(async () => {
  loadJobMap()
  await load()
})

onBeforeUnmount(stopPolling)
</script>

<template>
  <main class="page-content">
    <div class="page-heading page-heading-row">
      <div>
        <p class="eyebrow">资料库</p>
        <h1>资料</h1>
        <p class="intro">导入课程资料与通知，查看每一份资料的处理状态与未覆盖范围。</p>
        <p class="form-help">状态与未覆盖范围来自后端作业查询，不做本地推断；缺失内容不会被当作“没有要求”。</p>
      </div>
      <button type="button" @click="importOpen = !importOpen">{{ importOpen ? '收起导入' : '导入资料' }}</button>
    </div>

    <ApiFeedback v-if="error" :message="error" :busy="loading" @retry="load" />
    <template v-else>
      <section v-if="importOpen" class="panel editor-panel">
        <div class="section-heading">
          <div><p class="eyebrow">导入资料</p><h2>粘贴文本或上传文件</h2></div>
          <div class="segmented" role="tablist" aria-label="导入方式">
            <button type="button" class="segmented-item" :class="{ 'is-active': importMode === 'text' }" :aria-pressed="importMode === 'text'" @click="switchImportMode('text')">粘贴文本</button>
            <button type="button" class="segmented-item" :class="{ 'is-active': importMode === 'files' }" :aria-pressed="importMode === 'files'" @click="switchImportMode('files')">上传 PDF / 图片</button>
          </div>
        </div>

        <form v-if="importMode === 'text'" class="stack-form" @submit.prevent="submitText">
          <div class="form-grid">
            <label>标题<input v-model.trim="textForm.title" required maxlength="300" placeholder="例如：数据结构课程通知" /></label>
            <label>发布方<input v-model.trim="textForm.publisher" maxlength="200" placeholder="可选" /></label>
          </div>
          <label>正文<textarea v-model="textForm.content" rows="6" required placeholder="粘贴通知或课程要求原文"></textarea></label>
          <div class="form-grid form-grid-3">
            <label>学期<select v-model="textForm.semester_id" @change="textForm.course_id = ''"><option value="">未指定</option><option v-for="semester in semesters" :key="semester.id" :value="semester.id">{{ semester.name }}{{ semester.archived ? '（已归档）' : '' }}</option></select></label>
            <label>课程<select v-model="textForm.course_id"><option value="">未关联课程</option><option v-for="course in textCourses" :key="course.id" :value="course.id">{{ course.name }} · {{ course.class_name || '未分班' }}</option></select></label>
            <label>教学班<input v-model.trim="textForm.class_name" maxlength="100" placeholder="可选" /></label>
          </div>
          <label>发布日期<input v-model="textForm.published_at" type="date" /></label>
          <p class="form-help">只填日期时按工作空间时区解释为当地零点，不会自动补全时刻。适用范围应与资料实际所属学期、课程一致。</p>
          <p v-if="importError" class="form-error" role="alert">{{ importError }}</p>
          <p v-if="successMessage" class="success-message" role="status">{{ successMessage }}</p>
          <button type="submit" :disabled="importing">{{ importing ? '正在导入…' : '导入文本' }}</button>
        </form>

        <form v-else class="stack-form" @submit.prevent="submitUpload">
          <label>选择文件<input ref="fileInput" type="file" accept=".pdf,.png,.jpg,.jpeg,application/pdf,image/png,image/jpeg" multiple @change="onFilesChosen" /></label>
          <p class="form-help">支持 PDF、PNG、JPG/JPEG。完全相同的文件默认提示复用已有资料，避免重复建档。</p>
          <div class="form-grid form-grid-3">
            <label>学期<select v-model="uploadForm.semester_id" @change="uploadForm.course_id = ''"><option value="">未指定</option><option v-for="semester in semesters" :key="semester.id" :value="semester.id">{{ semester.name }}{{ semester.archived ? '（已归档）' : '' }}</option></select></label>
            <label>课程<select v-model="uploadForm.course_id"><option value="">未关联课程</option><option v-for="course in uploadCourses" :key="course.id" :value="course.id">{{ course.name }} · {{ course.class_name || '未分班' }}</option></select></label>
            <label>教学班<input v-model.trim="uploadForm.class_name" maxlength="100" placeholder="可选" /></label>
          </div>
          <label class="switch-field"><input v-model="uploadForm.allow_duplicate" type="checkbox" />允许重复导入相同文件</label>
          <p v-if="importError" class="form-error" role="alert">{{ importError }}</p>
          <button type="submit" :disabled="importing">{{ importing ? '正在上传…' : '上传并处理' }}</button>
        </form>

        <ul v-if="uploadResults" class="upload-results">
          <li v-for="(item, index) in uploadResults" :key="index">
            <strong>{{ item.filename }}</strong>
            <span v-if="item.material" class="status-pill">已建档 · 作业 #{{ item.job_id ?? '—' }}</span>
            <span v-else-if="item.duplicate_of" class="status-pill duplicate-pill">与已有资料相同 · 未重复建档</span>
            <span v-else class="status-pill failed-pill">{{ item.error }}</span>
          </li>
        </ul>
      </section>

      <section class="panel filter-bar" aria-label="资料筛选">
        <label>学期<select v-model="filters.semesterId" @change="filters.courseId = ''"><option value="">全部学期</option><option v-for="semester in semesters" :key="semester.id" :value="semester.id">{{ semester.name }}{{ semester.archived ? '（已归档）' : '' }}</option></select></label>
        <label>课程<select v-model="filters.courseId"><option value="">全部课程</option><option v-for="course in filteredCourses" :key="course.id" :value="course.id">{{ course.name }} · {{ course.class_name || '未分班' }}</option></select></label>
        <label>来源<select v-model="filters.sourceType"><option value="">全部来源</option><option v-for="(label, value) in sourceTypeLabels" :key="value" :value="value">{{ label }}</option></select></label>
        <label>状态<select v-model="filters.status"><option value="">全部状态</option><option v-for="(label, value) in statusLabels" :key="value" :value="value">{{ label }}</option></select></label>
        <label class="switch-field"><input v-model="filters.includeArchived" type="checkbox" @change="load" />包含已归档</label>
        <span class="count-badge">{{ visibleMaterials.length }} 份</span>
      </section>

      <section class="panel">
        <div class="section-heading">
          <div><p class="eyebrow">资料列表</p><h2>处理状态</h2></div>
          <button type="button" class="text-button" @click="manualRefresh">刷新</button>
        </div>
        <p v-if="pollPaused" class="inline-notice" role="status">自动刷新已暂停，请手动刷新查看最新处理状态。</p>
        <p v-if="loading" class="muted">正在加载资料…</p>
        <div v-else-if="visibleMaterials.length" class="material-list">
          <article v-for="material in visibleMaterials" :key="material.id" class="material-row">
            <div class="material-main">
              <div class="task-title-line">
                <h3>{{ material.title }}</h3>
                <span class="status-pill" :data-status="material.status">{{ statusLabels[material.status] }}</span>
                <span v-if="material.archived" class="status-pill archived-pill">已归档</span>
              </div>
              <p>{{ sourceTypeLabels[material.source_type] }} · {{ scopeText(material) }} · 导入于 {{ formatDateTime(material.imported_at) }}</p>
              <p v-if="material.publisher">发布方：{{ material.publisher }}{{ material.published_at ? ` · ${material.published_at.slice(0, 10)}` : '' }}</p>

              <div v-if="material.status === 'partial'" class="coverage-box">
                <strong>部分成功 · 未覆盖范围</strong>
                <p>{{ jobFor(material)?.scope ?? '正在获取未覆盖范围…' }}</p>
                <p class="form-help">缺失内容不会被当作“没有要求”；重试只补齐未识别的范围。</p>
              </div>
              <div v-else-if="material.status === 'failed'" class="coverage-box failed">
                <strong>处理失败</strong>
                <p>{{ jobFor(material)?.error_reason ?? '正在获取失败原因…' }}</p>
              </div>
            </div>
            <div class="material-actions">
              <button v-if="hasActiveJob(material)" type="button" class="text-button" disabled>处理中…</button>
              <button
                v-else-if="jobFor(material)?.retryable"
                type="button"
                class="button-secondary"
                :disabled="retryingId === material.id"
                @click="retry(material)"
              >{{ retryingId === material.id ? '正在重试…' : '重试处理' }}</button>
              <span v-else-if="material.status === 'failed'" class="muted small">不可重试</span>
              <button type="button" class="text-button" @click="sourceMaterial = material">查看原文</button>
            </div>
          </article>
        </div>
        <div v-else class="compact-empty">{{ materials.length ? '没有符合当前筛选的资料。' : '还没有资料，先导入一份课程通知或大纲。' }}</div>
      </section>

      <MaterialSource
        v-if="sourceMaterial"
        :material="sourceMaterial"
        :scope="scopeText(sourceMaterial)"
        @close="sourceMaterial = null"
      />
    </template>
  </main>
</template>
