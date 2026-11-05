<script setup lang="ts">
/*
 * 资料库页：导入（粘贴文本／文件／公开网页）、按筛选查看处理状态，并进入原文、归档与删除流程。
 * 流程：上传后逐项展示建档／重复／失败结果；处理中资料每 3 秒刷新解析作业，超过 10 分钟暂停自动刷新、改为手动。
 * 约束：状态与未覆盖范围只取自后端作业，不做本地推断；归档与删除分别处理，删除前必须先看影响预览。
 */
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'

import { coursesApi, semestersApi } from '../api/business'
import { errorMessage } from '../api/client'
import { jobsApi, materialsApi, type IndexProgress, type MaterialFilters } from '../api/library'
import type {
  Course,
  JobStage,
  Material,
  MaterialSourceType,
  MaterialStatus,
  MaterialUploadItem,
  ProcessingJob,
  Semester,
} from '../api/types'
import ApiFeedback from '../components/ApiFeedback.vue'
import MaterialLifecycle from '../components/MaterialLifecycle.vue'
import MaterialSource from '../components/MaterialSource.vue'

const POLL_INTERVAL_MS = 3000
// 处理中状态的自动刷新上限：超过后停止轮询，改为手动刷新，避免后台长时间不推进时空转。
const POLL_MAX_MS = 10 * 60 * 1000

const materials = ref<Material[]>([])
const semesters = ref<Semester[]>([])
const courses = ref<Course[]>([])
const loading = ref(true)
const error = ref('')

const importOpen = ref(false)
const importMode = ref<'text' | 'files' | 'web'>('text')
const importing = ref(false)
const importError = ref('')
const fallbackNotice = ref('')
const successMessage = ref('')
const uploadResults = ref<MaterialUploadItem[] | null>(null)

const textForm = reactive({
  process_ai: true,
  title: '', content: '', publisher: '', published_at: '', semester_id: '', course_id: '', class_name: '',
})
const webForm = reactive({
  url: '', title: '', publisher: '', published_at: '', published_text: '', semester_id: '', course_id: '', class_name: '',
})
const uploadForm = reactive({ semester_id: '', course_id: '', class_name: '', allow_duplicate: false })
const selectedFiles = ref<File[]>([])
const fileInput = ref<HTMLInputElement | null>(null)

const filters = reactive({ semesterId: '', courseId: '', sourceType: '', status: '', includeArchived: false })

/** 资料 → 最近一次解析作业的映射。作业从受授权后端恢复，刷新或更换浏览器后仍可查询范围与重试。 */
const jobByMaterial = reactive<Record<number, number>>({})
const jobs = reactive<Record<number, ProcessingJob>>({})
const jobErrors = reactive<Record<number, string>>({})
const currentJobs = reactive<Record<number, ProcessingJob[]>>({})
const indexProgress = reactive<Record<number, IndexProgress>>({})
const retryingId = ref<number | null>(null)
const sourceMaterial = ref<Material | null>(null)
const sourceVersionNo = ref<number | null>(null)
const sourceChunkId = ref<number | null>(null)
const lifecycleMaterial = ref<Material | null>(null)
const route = useRoute()

const pollPaused = ref(false)
let pollTimer: number | null = null
let pollStartedAt: number | null = null

const sourceTypeLabels: Record<MaterialSourceType, string> = {
  pdf: 'PDF', image: '图片', text: '粘贴文本', word: 'Word', web: '网页',
}
const statusLabels: Record<MaterialStatus, string> = {
  pending: '原文待读取', processing: '原文读取中', done: '原文读取完成', partial: '原文部分读取成功', failed: '原文读取失败',
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
const webCourses = computed(() => webForm.semester_id
  ? courses.value.filter((course) => course.semester_id === Number(webForm.semester_id))
  : courses.value)
const visibleMaterials = computed(() => materials.value)
const uploadSummary = computed(() => {
  if (!uploadResults.value) return ''
  const succeeded = uploadResults.value.filter((item) => item.material).length
  const duplicated = uploadResults.value.filter((item) => item.duplicate_of).length
  const failed = uploadResults.value.filter((item) => item.error).length
  const parts = [`成功 ${succeeded} 份`]
  if (duplicated) parts.push(`重复 ${duplicated} 份`)
  if (failed) parts.push(`失败 ${failed} 份`)
  return `本次导入：${parts.join('，')}。${failed ? '失败文件未建档，不影响其它文件。' : ''}`
})

function jobFor(material: Material): ProcessingJob | null {
  const jobId = jobByMaterial[material.id]
  return jobId ? jobs[jobId] ?? null : null
}

/** 仅当存在尚未进入终态的处理作业时才算“处理中”，避免对同步导入的资料无限轮询。 */
function hasActiveJob(material: Material): boolean {
  if (currentJobs[material.id]?.some((job) => !job.is_terminal)) return true
  const jobId = jobByMaterial[material.id]
  return jobId ? !(jobs[jobId]?.is_terminal ?? false) : false
}

function stageJob(material: Material, stage: JobStage): ProcessingJob | undefined {
  return currentJobs[material.id]?.find((job) => job.stage === stage)
}

function questionStatus(material: Material): string {
  if (material.archived) return '已归档，不参与问答'
  if (jobErrors[material.id]) return '状态未知，请刷新'
  const job = stageJob(material, 'embed')
  if (!job) return '未开始'
  if (!job.is_terminal) return '准备中'
  if (job.status === 'failed') return '准备失败'
  const summary = indexProgress[material.id]?.summary
  if (job.status === 'done' && summary?.fully_searchable) return '可问答'
  if (summary?.indexed) return '部分可用（有未覆盖内容）'
  return '尚不可问答'
}

function extractionStatus(material: Material): string {
  if (jobErrors[material.id]) return '状态未知，请刷新'
  const job = stageJob(material, 'extract')
  if (!job) return '未开始'
  return { pending: '等待整理', running: '整理中', done: '已整理，请到待核对查看', partial: '部分整理成功', failed: '整理失败' }[job.status]
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
  try {
    // 以最新资料版本为准，不能拿旧版本的失败或成功覆盖当前状态。
    const [allJobs, versions] = await Promise.all([jobsApi.forMaterial(material.id), materialsApi.versions(material.id)])
    const version = versions.reduce<(typeof versions)[number] | undefined>((latest, item) => !latest || item.version_no > latest.version_no ? item : latest, undefined)
    if (!version) throw new Error('missing material version')
    const active = allJobs.filter((job) => job.version_id === version?.id).sort((a, b) => b.id - a.id)
    currentJobs[material.id] = active
    const latest = active.find((job) => job.stage === 'parse')
    delete jobErrors[material.id]
    delete indexProgress[material.id]
    if (latest) {
      jobByMaterial[material.id] = latest.id
      jobs[latest.id] = latest
    } else {
      delete jobByMaterial[material.id]
    }
    const embed = active.find((job) => job.stage === 'embed')
    if (embed) indexProgress[material.id] = await jobsApi.indexProgress(embed.id)
  } catch (caught) {
    delete currentJobs[material.id]
    delete indexProgress[material.id]
    delete jobByMaterial[material.id]
    jobErrors[material.id] = `处理详情读取失败：${errorMessage(caught)}。请刷新重试。`
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
  const active = materials.value.some((material) => hasActiveJob(material) || material.status === 'processing' || (material.status === 'pending' && material.source_type !== 'text'))
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

async function retry(material: Material, stage: JobStage = 'parse') {
  const jobId = stageJob(material, stage)?.id
  retryingId.value = material.id
  error.value = ''
  try {
    if (!jobId) {
      error.value = '暂未读取到处理作业，请刷新后重试。'
      return
    }
    // 重试复用同一作业记录，后端只补齐未识别的范围，不会重复建档。
    jobs[jobId] = await jobsApi.retry(jobId)
    await refresh()
  } catch (caught) {
    error.value = errorMessage(caught)
  } finally {
    retryingId.value = null
  }
}

async function processMaterial(material: Material) {
  retryingId.value = material.id
  error.value = ''
  try {
    await materialsApi.process(material.id)
    successMessage.value = '已提交事项整理和问答准备；提取结果需要到“待核对”确认。'
    await refresh()
  } catch (caught) { error.value = errorMessage(caught) }
  finally { retryingId.value = null }
}

async function onArchiveChanged() {
  await refresh()
  const id = lifecycleMaterial.value?.id
  if (id !== undefined) {
    try {
      lifecycleMaterial.value = await materialsApi.get(id)
    } catch {
      // 归档后若不在当前筛选内，保留原对象供面板展示结果。
    }
  }
}

async function onMaterialDeleted(id: number) {
  delete jobByMaterial[id]
  delete jobErrors[id]
  if (sourceMaterial.value?.id === id) sourceMaterial.value = null
  // 保持生命周期面板打开，让用户看到“删除完成”的结果；列表在后台同步移除该资料。
  await refresh()
}

function openSource(material: Material) {
  sourceVersionNo.value = null
  sourceChunkId.value = null
  sourceMaterial.value = material
}

function queryNumber(value: unknown): number | null {
  const parsed = Number(value)
  return Number.isInteger(parsed) && parsed > 0 ? parsed : null
}

/** 核对界面的「定位原文」带上引用所属版本与片段，避免把旧版本的摘录定位到最新版本上。 */
async function openReferencedSource() {
  const materialId = queryNumber(route.query.material)
  if (materialId === null) return
  try {
    sourceMaterial.value = await materialsApi.get(materialId)
  } catch {
    // 资料已删除或不属于本空间时跳过定位，列表仍可正常查看。
    return
  }
  sourceVersionNo.value = queryNumber(route.query.version)
  sourceChunkId.value = queryNumber(route.query.chunk)
}

function onFilesChosen(event: Event) {
  const input = event.target as HTMLInputElement
  selectedFiles.value = Array.from(input.files ?? [])
}

async function submitUpload() {
  if (!selectedFiles.value.length) {
    importError.value = '请先选择要导入的 PDF、Word（DOCX）或图片文件。'
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
    fallbackNotice.value = ''
    // 逐文件结果：material 为已建档，duplicate_of 为复用已有资料（未重复建档），error 为该文件失败；
    // 失败文件不建档且不影响其它文件，只有建档成功的项才登记作业并进入轮询。
    result.results.forEach((item) => {
      if (item.material && item.job_id) {
        jobByMaterial[item.material.id] = item.job_id
      }
    })
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
    // 原文同步保存，用户选择的 AI 阶段由后台处理，前端轮询实际作业。
    const result = await materialsApi.importText({
      process_ai: textForm.process_ai,
      title: textForm.title.trim(),
      content: textForm.content,
      publisher: textForm.publisher.trim() || null,
      published_at: textForm.published_at || null,
      semester_id: textForm.semester_id ? Number(textForm.semester_id) : null,
      course_id: textForm.course_id ? Number(textForm.course_id) : null,
      class_name: textForm.class_name.trim() || null,
    })
    successMessage.value = `已导入「${result.material.title}」，切分 ${result.chunks.length} 个段落。${textForm.process_ai ? '事项整理和问答准备已提交，结果请到待核对查看。' : '仅保存原文，尚未启动 AI 处理。'}`
    fallbackNotice.value = ''
    Object.assign(textForm, { title: '', content: '', publisher: '', published_at: '', class_name: '' })
    await refresh()
  } catch (caught) {
    importError.value = errorMessage(caught)
  } finally {
    importing.value = false
  }
}

async function submitWeb() {
  if (!webForm.url.trim() || !webForm.title.trim()) {
    importError.value = '网页网址与标题都不能为空。'
    return
  }
  importing.value = true
  importError.value = ''
  successMessage.value = ''
  uploadResults.value = null
  try {
    // 网页快照与解析作业由后端一起创建，成功后靠作业轮询更新处理状态。
    const result = await materialsApi.importWeb({
      url: webForm.url.trim(),
      title: webForm.title.trim(),
      publisher: webForm.publisher.trim() || null,
      published_at: webForm.published_at.trim() || null,
      published_text: webForm.published_text.trim() || null,
      semester_id: webForm.semester_id ? Number(webForm.semester_id) : null,
      course_id: webForm.course_id ? Number(webForm.course_id) : null,
      class_name: webForm.class_name.trim() || null,
    })
    jobByMaterial[result.material.id] = result.job_id
    successMessage.value = `已保存「${result.material.title}」的网页快照，解析作业 #${result.job_id} 已创建。`
    fallbackNotice.value = ''
    Object.assign(webForm, { url: '', title: '', publisher: '', published_at: '', published_text: '', class_name: '' })
    await refresh()
  } catch (caught) {
    importError.value = errorMessage(caught)
    // 失败时后端不会保存网页快照；明确未覆盖范围，并提供粘贴/上传入口。
    fallbackNotice.value = `网页正文未导入：${importError.value}。可粘贴可访问的原文或上传已保存的文件；未覆盖内容不会被当作“没有要求”。`
  } finally {
    importing.value = false
  }
}

function useWebFallback(target: 'text' | 'files') {  const notice = fallbackNotice.value
  if (target === 'text') {
    Object.assign(textForm, {
      title: webForm.title,
      publisher: webForm.publisher,
      published_at: webForm.published_at,
      semester_id: webForm.semester_id,
      course_id: webForm.course_id,
      class_name: webForm.class_name,
    })
  } else {
    Object.assign(uploadForm, {
      semester_id: webForm.semester_id,
      course_id: webForm.course_id,
      class_name: webForm.class_name,
    })
  }
  importMode.value = target
  importError.value = ''
  successMessage.value = ''
  uploadResults.value = null
  fallbackNotice.value = notice
}

// 网页资料部分成功或失败时，把已知元数据带入替代入口，避免重复填写，同时保留未覆盖范围提示。
function useMaterialFallback(material: Material, target: 'text' | 'files') {
  if (target === 'text') {
    Object.assign(textForm, {
      title: material.title,
      publisher: material.publisher ?? '',
      published_at: material.published_at ?? '',
      class_name: material.class_name ?? '',
    })
  } else {
    Object.assign(uploadForm, { class_name: material.class_name ?? '' })
  }
  importOpen.value = true
  importMode.value = target
  importError.value = ''
  successMessage.value = ''
  uploadResults.value = null
  const reason = jobFor(material)?.error_reason ?? '未返回失败原因，请刷新查询处理详情'
  fallbackNotice.value = `「${material.title}」的网页正文未识别：${reason}。可粘贴原文或上传已保存的文件；未覆盖内容不会被当作“没有要求”。`
}

function switchImportMode(mode: 'text' | 'files' | 'web') {
  importMode.value = mode
  importError.value = ''
  successMessage.value = ''
  fallbackNotice.value = ''
  uploadResults.value = null
}

onMounted(async () => {
  await load()
  await openReferencedSource()
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
          <div><p class="eyebrow">导入资料</p><h2>粘贴文本、上传文件或读取网页</h2></div>
          <div class="segmented" role="tablist" aria-label="导入方式">
            <button type="button" class="segmented-item" :class="{ 'is-active': importMode === 'text' }" :aria-pressed="importMode === 'text'" @click="switchImportMode('text')">粘贴文本</button>
            <button type="button" class="segmented-item" :class="{ 'is-active': importMode === 'files' }" :aria-pressed="importMode === 'files'" @click="switchImportMode('files')">上传文件</button>
            <button type="button" class="segmented-item" :class="{ 'is-active': importMode === 'web' }" :aria-pressed="importMode === 'web'" @click="switchImportMode('web')">公开网页</button>
          </div>
        </div>

        <p v-if="fallbackNotice" class="inline-notice" role="status">{{ fallbackNotice }}</p>

        <form v-if="importMode === 'text'" class="stack-form" @submit.prevent="submitText">
          <div class="form-grid">
            <label>标题<input v-model.trim="textForm.title" required maxlength="300" placeholder="例如：数据结构课程通知" /></label>
            <label>发布方<input v-model.trim="textForm.publisher" maxlength="200" placeholder="可选" /></label>
          </div>
          <label>正文<textarea v-model="textForm.content" rows="6" required placeholder="粘贴通知或课程要求原文"></textarea></label>
          <label><input v-model="textForm.process_ai" type="checkbox" />导入后整理事项并准备问答（会调用已配置的模型，可能产生费用）</label>
          <p class="form-help">取消勾选只保存原文。整理结果进入“待核对”，不会自动生成正式任务。</p>
          <div class="form-grid form-grid-3">
            <label>学期<select v-model="textForm.semester_id" @change="textForm.course_id = ''"><option value="">未指定</option><option v-for="semester in semesters" :key="semester.id" :value="semester.id">{{ semester.name }}{{ semester.archived ? '（已归档）' : '' }}</option></select></label>
            <label>课程<select v-model="textForm.course_id"><option value="">未关联课程</option><option v-for="course in textCourses" :key="course.id" :value="course.id">{{ course.name }} · {{ course.class_name || '未分班' }}</option></select></label>
            <label>教学班<input v-model.trim="textForm.class_name" maxlength="100" placeholder="可选" /></label>
          </div>
          <label>发布时间（完整时间与时区，可选）<input v-model="textForm.published_at" placeholder="例如：2026-10-13T10:30:00+08:00" /></label>
          <p class="form-help">只在来源给出完整时刻时填写；只有日期时保留在正文，不补造零点。适用范围应与资料实际所属学期、课程一致。</p>
          <p v-if="importError" class="form-error" role="alert">{{ importError }}</p>
          <p v-if="successMessage" class="success-message" role="status">{{ successMessage }}</p>
          <button type="submit" :disabled="importing">{{ importing ? '正在导入…' : '导入文本' }}</button>
        </form>

        <form v-else-if="importMode === 'web'" class="stack-form" @submit.prevent="submitWeb">
          <div class="form-grid">
            <label>网页网址<input v-model.trim="webForm.url" type="url" required maxlength="500" placeholder="https://example.edu/notice" /></label>
            <label>标题<input v-model.trim="webForm.title" required maxlength="300" placeholder="例如：数据结构课程通知" /></label>
          </div>
          <div class="form-grid form-grid-3">
            <label>学期<select v-model="webForm.semester_id" @change="webForm.course_id = ''"><option value="">未指定</option><option v-for="semester in semesters" :key="semester.id" :value="semester.id">{{ semester.name }}{{ semester.archived ? '（已归档）' : '' }}</option></select></label>
            <label>课程<select v-model="webForm.course_id"><option value="">未关联课程</option><option v-for="course in webCourses" :key="course.id" :value="course.id">{{ course.name }} · {{ course.class_name || '未分班' }}</option></select></label>
            <label>教学班<input v-model.trim="webForm.class_name" maxlength="100" placeholder="可选" /></label>
          </div>
          <div class="form-grid">
            <label>发布方<input v-model.trim="webForm.publisher" maxlength="200" placeholder="可选" /></label>
            <label>发布时间（含时区，可选）<input v-model.trim="webForm.published_at" placeholder="例如：2026-10-13T10:30:00+08:00" /></label>
          </div>
          <label>发布日期原文（可选）<input v-model.trim="webForm.published_text" maxlength="200" placeholder="例如：2026-11-20" /></label>
          <p class="form-help">只读取公开页面，不会自动登录教务系统、提交表单或绕过登录/反爬验证。只有日期时填写原文，不补造时刻；读取失败可改用粘贴或上传。</p>
          <p v-if="importError" class="form-error" role="alert">{{ importError }}</p>
          <p v-if="successMessage" class="success-message" role="status">{{ successMessage }}</p>
          <div v-if="fallbackNotice" class="material-actions">
            <button type="button" class="button-secondary" @click="useWebFallback('text')">改为粘贴原文</button>
            <button type="button" class="button-secondary" @click="useWebFallback('files')">改为上传文件</button>
          </div>
          <button type="submit" :disabled="importing">{{ importing ? '正在读取…' : '读取网页并处理' }}</button>
        </form>

        <form v-else class="stack-form" @submit.prevent="submitUpload">
          <label>选择文件<input ref="fileInput" type="file" accept=".pdf,.docx,.png,.jpg,.jpeg,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,image/png,image/jpeg" multiple @change="onFilesChosen" /></label>
          <p class="form-help">支持 PDF、Word（DOCX）、PNG、JPG/JPEG。旧版 DOC 或宏文档请先另存为普通 DOCX；网页通知可使用“公开网页”入口。完全相同的文件默认提示复用已有资料，避免重复建档。</p>
          <div class="form-grid form-grid-3">
            <label>学期<select v-model="uploadForm.semester_id" @change="uploadForm.course_id = ''"><option value="">未指定</option><option v-for="semester in semesters" :key="semester.id" :value="semester.id">{{ semester.name }}{{ semester.archived ? '（已归档）' : '' }}</option></select></label>
            <label>课程<select v-model="uploadForm.course_id"><option value="">未关联课程</option><option v-for="course in uploadCourses" :key="course.id" :value="course.id">{{ course.name }} · {{ course.class_name || '未分班' }}</option></select></label>
            <label>教学班<input v-model.trim="uploadForm.class_name" maxlength="100" placeholder="可选" /></label>
          </div>
          <label class="switch-field"><input v-model="uploadForm.allow_duplicate" type="checkbox" />允许重复导入相同文件</label>
          <p v-if="importError" class="form-error" role="alert">{{ importError }}</p>
          <button type="submit" :disabled="importing">{{ importing ? '正在上传…' : '上传并处理' }}</button>
        </form>

        <p v-if="uploadResults" :class="uploadResults.some((item) => item.error) ? 'inline-notice' : 'form-help'" role="status">{{ uploadSummary }}</p>
        <!-- 逐文件结果逐条展示：失败项不并入成功数，重复项不新建资料。 -->
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
        <label>学期<select v-model="filters.semesterId" @change="filters.courseId = ''; load()"><option value="">全部学期</option><option v-for="semester in semesters" :key="semester.id" :value="semester.id">{{ semester.name }}{{ semester.archived ? '（已归档）' : '' }}</option></select></label>
        <label>课程<select v-model="filters.courseId" @change="load"><option value="">全部课程</option><option v-for="course in filteredCourses" :key="course.id" :value="course.id">{{ course.name }} · {{ course.class_name || '未分班' }}</option></select></label>
        <label>来源<select v-model="filters.sourceType" @change="load"><option value="">全部来源</option><option v-for="(label, value) in sourceTypeLabels" :key="value" :value="value">{{ label }}</option></select></label>
        <label>状态<select v-model="filters.status" @change="load"><option value="">全部状态</option><option v-for="(label, value) in statusLabels" :key="value" :value="value">{{ label }}</option></select></label>
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
              <p v-if="jobErrors[material.id]" class="form-error" role="alert">{{ jobErrors[material.id] }}</p>
              <div class="coverage-box" aria-label="资料处理阶段">
                <p><strong>事项整理：{{ extractionStatus(material) }}</strong></p>
                <p v-if="stageJob(material, 'extract')?.scope">{{ stageJob(material, 'extract')?.scope }}</p>
                <p v-if="stageJob(material, 'extract')?.error_reason">{{ stageJob(material, 'extract')?.error_reason }}</p>
                <button v-if="stageJob(material, 'extract')?.retryable || stageJob(material, 'extract')?.recovery_action === 'configure_ai'" type="button" class="text-button" :disabled="retryingId === material.id" @click="retry(material, 'extract')">重试事项整理</button>
                <p><strong>资料问答：{{ questionStatus(material) }}</strong></p>
                <p v-if="stageJob(material, 'embed')?.error_reason">{{ stageJob(material, 'embed')?.error_reason }}</p>
                <p v-if="indexProgress[material.id]?.summary" class="form-help">已准备 {{ indexProgress[material.id]?.summary?.indexed }} / {{ indexProgress[material.id]?.summary?.expected }} 段；原文未覆盖 {{ indexProgress[material.id]?.summary?.uncovered_source_count }} 处{{ indexProgress[material.id]?.summary?.source_coverage_known ? '' : '（原文覆盖范围尚未确认）' }}。</p>
                <p v-if="stageJob(material, 'embed')?.error_reason || stageJob(material, 'extract')?.error_reason" class="form-help">请根据失败原因检查 AI 服务设置或补充资料；已读出的原文仍可查看。</p>
                <RouterLink v-if="stageJob(material, 'embed')?.status === 'failed' || stageJob(material, 'extract')?.status === 'failed'" to="/settings#ai-settings">打开 AI 服务设置</RouterLink>
                <button v-if="stageJob(material, 'embed')?.retryable || stageJob(material, 'embed')?.recovery_action === 'configure_ai'" type="button" class="text-button" :disabled="retryingId === material.id" @click="retry(material, 'embed')">重试问答准备</button>
              </div>

              <!-- 部分成功必须展示未覆盖范围与失败原因；缺失内容不能被当作“没有要求”。 -->
              <div v-if="material.status === 'partial'" class="coverage-box">
                <strong>部分成功 · 未覆盖范围</strong>
                <p>{{ jobFor(material)?.scope ?? jobErrors[material.id] ?? '未返回覆盖范围，请刷新查询处理详情。' }}</p>
                <p v-if="jobFor(material)?.error_reason">{{ jobFor(material)?.error_reason }}</p>
                <p class="form-help">缺失内容不会被当作“没有要求”；重试只补齐未识别的范围。</p>
                <div v-if="material.source_type === 'web'" class="material-actions">
                  <button type="button" class="text-button" @click="useMaterialFallback(material, 'text')">粘贴网页原文</button>
                  <button type="button" class="text-button" @click="useMaterialFallback(material, 'files')">上传网页文件</button>
                </div>
              </div>
              <div v-else-if="material.status === 'failed'" class="coverage-box failed">
                <strong>处理失败</strong>
                <p>{{ jobFor(material)?.error_reason ?? jobErrors[material.id] ?? '未返回失败原因，请刷新查询处理详情。' }}</p>
                <div v-if="material.source_type === 'web'" class="material-actions">
                  <button type="button" class="text-button" @click="useMaterialFallback(material, 'text')">粘贴网页原文</button>
                  <button type="button" class="text-button" @click="useMaterialFallback(material, 'files')">上传网页文件</button>
                </div>
              </div>
              <p v-if="jobFor(material)?.recovery_action === 'configure_ai'" class="form-help">请先<RouterLink to="/settings#ai-settings">检查 AI 服务设置</RouterLink>，保存后返回此处手动重试。已有原文会保留。</p>
              <p v-if="!material.archived && !jobErrors[material.id] && (material.status === 'done' || material.status === 'partial') && (!stageJob(material, 'extract') || !stageJob(material, 'embed'))" class="form-help">
                <button type="button" class="button-secondary" :disabled="retryingId === material.id" @click="processMaterial(material)">继续整理与问答准备</button> 将调用已配置模型，可能产生费用；提取结果需人工核对。
              </p>
            </div>
            <div class="material-actions">
              <button v-if="hasActiveJob(material)" type="button" class="text-button" disabled>处理中…</button>
              <button
                v-else-if="jobFor(material)?.retryable || jobFor(material)?.recovery_action === 'configure_ai'"
                type="button"
                class="button-secondary"
                :disabled="retryingId === material.id"
                @click="retry(material)"
              >{{ retryingId === material.id ? '正在重试…' : '重试处理' }}</button>
              <span v-else-if="material.status === 'failed' && jobFor(material)" class="muted small">不可重试</span>
              <button type="button" class="text-button" @click="openSource(material)">查看原文</button>
              <button type="button" class="text-button" @click="lifecycleMaterial = material">归档 / 删除</button>
            </div>
          </article>
        </div>
        <div v-else class="compact-empty">{{ materials.length ? '没有符合当前筛选的资料。' : '还没有资料，先导入一份课程通知或大纲。' }}</div>
      </section>

      <MaterialSource
        v-if="sourceMaterial"
        :key="`${sourceMaterial.id}-${sourceVersionNo ?? 'latest'}-${sourceChunkId ?? 'first'}`"
        :material="sourceMaterial"
        :scope="scopeText(sourceMaterial)"
        :initial-version-no="sourceVersionNo"
        :initial-chunk-id="sourceChunkId"
        @close="sourceMaterial = null"
      />

      <!-- 删除影响预览与确认令牌在子组件内完成；确认只覆盖预览展示过的范围，内容变化需重新预览。 -->
      <MaterialLifecycle
        v-if="lifecycleMaterial"
        :key="lifecycleMaterial.id"
        :material="lifecycleMaterial"
        @close="lifecycleMaterial = null"
        @archive-changed="onArchiveChanged"
        @deleted="onMaterialDeleted"
      />
    </template>
  </main>
</template>
