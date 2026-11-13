<script setup lang="ts">
/*
 * 资料库页：导入（粘贴文本／文件／公开网页）、按筛选查看处理状态，并进入原文、归档与删除流程。
 * 流程：上传后逐项展示建档／重复／失败结果；处理中资料每 3 秒刷新解析作业，超过 10 分钟暂停自动刷新、改为手动；
 * 轮询只补查仍在处理或尚未取到详情的资料，学期与课程只在首次加载和显式刷新时读取，标签页隐藏时暂停轮询。
 * 约束：状态与未覆盖范围只取自后端作业，不做本地推断；归档与删除分别处理，删除前必须先看影响预览。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import type { Ref } from 'vue'
import { useRoute } from 'vue-router'

import { coursesApi, semestersApi } from '../api/business'
import { ApiError, errorMessage } from '../api/client'
import { jobsApi, materialsApi, type MaterialFilters } from '../api/library'
import type {
  Course,
  IndexState,
  Material,
  MaterialRecognition,
  MaterialSourceType,
  MaterialStatus,
  MaterialUploadItem,
  ProcessingJob,
  Semester,
  TextReadState,
} from '../api/types'
import ApiFeedback from '../components/ApiFeedback.vue'
import MaterialLifecycle from '../components/MaterialLifecycle.vue'
import MaterialSource from '../components/MaterialSource.vue'
import { formatDateTime } from '../utils/datetime'
import { parsePositiveInt } from '../utils/query'

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
// 行内操作错误（重试处理／重试索引／开始识别）：只影响该行，不参与整页 v-if，避免替换掉列表与导入表单。
const actionErrors = reactive<Record<number, string>>({})
// 本轮已取过作业／识别详情的资料；轮询据此只补查仍在处理或首次出现的数据，不再对全部资料逐条请求。
const jobChecked = new Set<number>()
const recognitionChecked = new Set<number>()
const retryingId = ref<number | null>(null)
const retryingIndexId = ref<number | null>(null)
// 粘贴文本的识别状态：原文保存完成不等于问答索引或事项提取完成。
const recognitionByMaterial = reactive<Record<number, MaterialRecognition>>({})
const recognitionErrors = reactive<Record<number, string>>({})
const recognizingId = ref<number | null>(null)
const recognitionNotice = ref('')
const sourceMaterial = ref<Material | null>(null)
const sourceVersionNo = ref<number | null>(null)
const sourceChunkId = ref<number | null>(null)
const lifecycleMaterial = ref<Material | null>(null)
// 原文／归档删除面板的组件实例：只用于取根元素做滚动与聚焦，不参与业务状态。
type PanelInstance = { $el?: unknown }
const sourcePanel = ref<PanelInstance | null>(null)
const lifecyclePanel = ref<PanelInstance | null>(null)
// 打开面板前记住触发按钮，关闭后把焦点还回去，键盘与读屏用户不丢失所在位置。
let sourceTrigger: HTMLElement | null = null
let lifecycleTrigger: HTMLElement | null = null
const route = useRoute()

const pollPaused = ref(false)
let pollTimer: number | null = null
let pollStartedAt: number | null = null
// 标签页隐藏时暂停轮询，避免后台空转；恢复可见后立即补齐一次。
let pageHidden = false
// 列表请求序号：筛选、导入、轮询与显式刷新可能并发，只有最后一次请求可以写回列表与错误。
let listRequestId = 0
let unmounted = false

/** 开始一次列表请求并取得序号；响应回来时用 isCurrentListRequest 判断是否仍有效。 */
function beginListRequest(): number {
  return ++listRequestId
}

function isCurrentListRequest(requestId: number): boolean {
  return !unmounted && requestId === listRequestId
}

const sourceTypeLabels: Record<MaterialSourceType, string> = {
  pdf: 'PDF', image: '图片', text: '粘贴文本', word: 'Word', web: '网页',
}
// 旧 status 字段只用于筛选原文解析状态，展示改用下面两条独立状态轴。
const statusLabels: Record<MaterialStatus, string> = {
  pending: '待读取', processing: '读取中', done: '已读取', partial: '部分读取', failed: '读取失败',
}
// 列表同时展示两条状态轴（#555）：原文读取完成不等于可以问答，索引失败必须可见。
const textStateLabels: Record<TextReadState, string> = {
  queued: '原文待读取', reading: '原文读取中', ready: '原文已读取', partial: '原文部分读取', failed: '原文读取失败',
}
const indexStateLabels: Record<IndexState, string> = {
  not_started: '问答索引未开始', preparing: '问答索引准备中', ready: '问答索引已准备',
  partial: '问答索引部分准备', failed: '问答索引失败', unknown: '问答索引待核对',
}
const semesterMap = computed(() => new Map(semesters.value.map((item) => [item.id, item])))
const courseMap = computed(() => new Map(courses.value.map((item) => [item.id, item])))

/** 按学期过滤课程：未选学期（空串）时列出全部课程，筛选与三个导入表单共用。 */
function coursesInSemester(list: Course[], semesterId: string): Course[] {
  return semesterId ? list.filter((course) => course.semester_id === Number(semesterId)) : list
}

const filteredCourses = computed(() => coursesInSemester(courses.value, filters.semesterId))
const uploadCourses = computed(() => coursesInSemester(courses.value, uploadForm.semester_id))
const textCourses = computed(() => coursesInSemester(courses.value, textForm.semester_id))
const webCourses = computed(() => coursesInSemester(courses.value, webForm.semester_id))
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

function recognitionFor(material: Material): MaterialRecognition | null {
  return recognitionByMaterial[material.id] ?? null
}

/** 仅当存在尚未进入终态的处理作业时才算“处理中”，避免对同步导入的资料无限轮询。 */
function hasActiveJob(material: Material): boolean {
  const jobId = jobByMaterial[material.id]
  return jobId ? !(jobs[jobId]?.is_terminal ?? false) : false
}

/** 粘贴文本的识别（问答索引或事项提取）仍在进行时需要继续刷新。 */
function hasRecognitionInProgress(material: Material): boolean {
  if (material.source_type !== 'text') return false
  const state = recognitionByMaterial[material.id]
  if (!state) return false
  return state.index.state === 'queued'
    || state.index.state === 'running'
    || state.extraction.state === 'running'
}

/** 轮询只补查仍有活动作业或还没取过作业详情的资料，避免对全部资料逐条请求。 */
function needsJobLookup(material: Material): boolean {
  return hasActiveJob(material) || !jobChecked.has(material.id)
}

/** 识别状态同理：只补查进行中的粘贴文本，以及本轮尚未取过状态的资料。 */
function needsRecognitionLookup(material: Material): boolean {
  if (material.source_type !== 'text') return false
  return hasRecognitionInProgress(material) || !recognitionChecked.has(material.id)
}

/** 读取粘贴文本的真实识别状态；失败只影响该行，不清空其它资料。 */
async function ensureRecognition(material: Material) {
  if (material.source_type !== 'text') return
  recognitionChecked.add(material.id)
  try {
    recognitionByMaterial[material.id] = await materialsApi.recognition(material.id)
    delete recognitionErrors[material.id]
  } catch (caught) {
    recognitionErrors[material.id] = `识别状态读取失败：${errorMessage(caught)}。请刷新重试。`
  }
}

const indexRecognitionLabels: Record<MaterialRecognition['index']['state'], string> = {
  not_started: '尚未准备',
  queued: '排队中',
  running: '处理中',
  done: '已准备',
  partial: '部分完成',
  failed: '失败',
}
const extractionRecognitionLabels: Record<MaterialRecognition['extraction']['state'], string> = {
  not_started: '尚未提取',
  running: '识别中',
  done: '已提取候选事项',
  empty: '未提取到任务',
  failed: '失败',
}

/** 行内摘要只使用后端返回的真实状态，不根据资料 status 推断识别完成。 */
function recognitionSummary(material: Material): { tone: string; text: string } {
  const state = recognitionByMaterial[material.id]
  if (!state) return { tone: 'pending', text: '识别状态读取中' }
  if (state.archived) return { tone: 'failed', text: '已归档，识别入口不可用' }
  if (state.extraction.state === 'running') return { tone: 'processing', text: '正在识别事项' }
  if (state.extraction.state === 'failed') return { tone: 'failed', text: '事项识别失败' }
  if (state.index.state === 'failed') return { tone: 'failed', text: '问答索引失败' }
  if (state.extraction.state === 'done') {
    return { tone: 'done', text: `已提取 ${state.extraction.candidate_count} 条候选事项` }
  }
  if (state.extraction.state === 'empty') return { tone: 'partial', text: '未提取到任务' }
  // 索引准备好只表示检索数据可用；问答界面当前暂未提供入口，不写“可问答”。
  if (state.index.state === 'done') return { tone: 'partial', text: '问答索引已准备，尚未提取事项' }
  if (state.index.state === 'queued' || state.index.state === 'running') {
    return { tone: 'processing', text: '问答索引处理中' }
  }
  return { tone: 'pending', text: '尚未准备问答索引或提取事项' }
}

function recognitionActionLabel(material: Material): string {
  const state = recognitionByMaterial[material.id]
  if (!state) return '开始识别'
  // 事项已完成但索引仍在排队：按钮只用于补投索引，不重复调用模型。
  if (state.index.state === 'queued' && (state.extraction.state === 'done' || state.extraction.state === 'empty')) {
    return '重新尝试准备问答'
  }
  const retry = state.extraction.state === 'failed'
    || state.index.state === 'failed'
    || state.index.state === 'partial'
  return retry ? '重新识别' : '开始识别'
}

function recognitionIndexText(material: Material): string {
  const state = recognitionFor(material)
  if (!state) return '读取中'
  const reason = state.index.reason ? `（${state.index.reason}）` : ''
  return `${indexRecognitionLabels[state.index.state]}${reason}`
}

function recognitionExtractionText(material: Material): string {
  const state = recognitionFor(material)
  if (!state) return '读取中'
  const reason = state.extraction.reason ? `（${state.extraction.reason}）` : ''
  return `${extractionRecognitionLabels[state.extraction.state]}${reason}`
}

function recognitionCanStart(material: Material): boolean {
  return recognitionFor(material)?.can_start ?? false
}

function recognitionBlockedReason(material: Material): string | null {
  return recognitionFor(material)?.blocked_reason ?? null
}

/** 只有用户显式点击才调用模型；请求期间禁用按钮，避免并发重复触发。 */
async function startRecognition(material: Material) {
  recognizingId.value = material.id
  recognitionNotice.value = ''
  delete actionErrors[material.id]
  try {
    const result = await materialsApi.startRecognition(material.id)
    recognitionByMaterial[material.id] = result
    recognitionNotice.value = `「${material.title}」：${result.message}`
    await refresh()
  } catch (caught) {
    // 行内提示失败原因，避免随后的状态重读把错误覆盖掉。
    actionErrors[material.id] = `开始识别失败：${errorMessage(caught)}`
    // 失败后重新读取状态，区分“识别失败”“配置缺失”和“确实没有事项”。
    await ensureRecognition(material)
  } finally {
    recognizingId.value = null
  }
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

async function ensureJob(material: Material) {
  jobChecked.add(material.id)
  try {
    // 只取 parse 阶段最新作业；同步导入（如粘贴文本）没有作业时清除本地关联，避免轮询旧记录。
    const latest = (await jobsApi.forMaterial(material.id)).find((job) => job.stage === 'parse')
    delete jobErrors[material.id]
    if (latest) {
      jobByMaterial[material.id] = latest.id
      jobs[latest.id] = latest
    } else {
      delete jobByMaterial[material.id]
    }
  } catch (caught) {
    jobErrors[material.id] = `处理详情读取失败：${errorMessage(caught)}。请刷新重试。`
  }
}

/** 只补查需要更新的资料；已到终态且本轮取过详情的资料不再重复请求。 */
async function loadDetails(list: Material[]) {
  await Promise.all([
    ...list.filter(needsJobLookup).map(ensureJob),
    ...list.filter(needsRecognitionLookup).map(ensureRecognition),
  ])
}

/** 首次加载与用户显式刷新：资料、学期与课程一起读取，并重新核对每份资料的详情。 */
async function load() {
  const requestId = beginListRequest()
  loading.value = true
  error.value = ''
  try {
    const [materialList, semesterList, courseList] = await Promise.all([
      materialsApi.list(currentFilters()), semestersApi.list(true), coursesApi.list(),
    ])
    if (unmounted) return
    // 学期与课程不随筛选变化：即使列表已被更新的请求取代，也保留已读取的基础数据。
    semesters.value = semesterList
    courses.value = courseList
    // 期间筛选或刷新已发起新请求：旧列表（含错误）直接丢弃，不能覆盖新范围。
    if (!isCurrentListRequest(requestId)) return
    materials.value = materialList
    // 显式刷新会清空行内操作错误，并重新取一次全部详情。
    jobChecked.clear()
    recognitionChecked.clear()
    Object.keys(actionErrors).forEach((key) => delete actionErrors[Number(key)])
    await Promise.all(materialList.map(ensureJob))
    if (!isCurrentListRequest(requestId)) return
    await Promise.all(materialList.map(ensureRecognition))
  } catch (caught) {
    if (!isCurrentListRequest(requestId)) return
    error.value = errorMessage(caught)
  } finally {
    if (!unmounted) {
      loading.value = false
      syncPolling()
    }
  }
}

/** 只重新读取资料列表（筛选、导入、归档与删除后）；学期与课程沿用已加载的基础数据，不重复拉取。 */
async function reloadMaterials() {
  const requestId = beginListRequest()
  try {
    const materialList = await materialsApi.list(currentFilters())
    if (!isCurrentListRequest(requestId)) return
    materials.value = materialList
    await loadDetails(materialList)
  } catch (caught) {
    if (!isCurrentListRequest(requestId)) return
    error.value = errorMessage(caught)
  } finally {
    if (!unmounted) syncPolling()
  }
}

/** 轮询：重新读取列表并补查仍在处理的资料；偶发失败保留上一次结果，不打断用户。 */
async function refresh() {
  const requestId = beginListRequest()
  try {
    const materialList = await materialsApi.list(currentFilters())
    if (!isCurrentListRequest(requestId)) return
    materials.value = materialList
    await loadDetails(materialList)
  } catch {
    // 轮询中的偶发失败保留上一次结果，不打断用户。
  } finally {
    if (!unmounted) syncPolling()
  }
}

/** 标签页隐藏时暂停轮询，恢复可见时先补齐一次，再按原上限恢复自动刷新。 */
function onVisibilityChange() {
  pageHidden = document.visibilityState === 'hidden'
  if (pageHidden) {
    clearPollTimer()
    return
  }
  void refresh()
}

function syncPolling() {
  // 解析/索引作业、两条状态轴或粘贴文本识别仍在进行时保持轮询；
  // 同步导入（无作业）且识别已完成时不轮询。
  const active = materials.value.some((material) =>
    hasActiveJob(material)
    || hasRecognitionInProgress(material)
    || material.processing.text_state === 'queued'
    || material.processing.text_state === 'reading'
    || material.processing.index.state === 'preparing')
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
  if (pageHidden) {
    // 隐藏时只停止定时器，保留计时起点，恢复可见后仍受 10 分钟上限约束。
    clearPollTimer()
    return
  }
  if (pollTimer === null) pollTimer = window.setInterval(refresh, POLL_INTERVAL_MS)
}

function clearPollTimer() {
  if (pollTimer !== null) {
    window.clearInterval(pollTimer)
    pollTimer = null
  }
}

function stopPolling() {
  clearPollTimer()
  pollStartedAt = null
}

async function manualRefresh() {
  // 首次加载或手动刷新仍在进行时不重复发起，避免旧请求先返回后又显示一次加载中。
  if (loading.value) return
  pollPaused.value = false
  // 用户显式刷新：连同学期与课程一起重新校准。
  await load()
}

async function retry(material: Material) {
  const jobId = jobByMaterial[material.id]
  retryingId.value = material.id
  // 行内错误只影响这一行：失败后列表、筛选与导入表单保持可用，不切换到整页错误页。
  delete actionErrors[material.id]
  try {
    if (!jobId) {
      actionErrors[material.id] = '暂未读取到处理作业，请刷新后重试。'
      return
    }
    // 重试复用同一作业记录，后端只补齐未识别的范围，不会重复建档。
    jobs[jobId] = await jobsApi.retry(jobId)
    await refresh()
  } catch (caught) {
    actionErrors[material.id] = `重试处理失败：${errorMessage(caught)}`
  } finally {
    retryingId.value = null
  }
}

async function retryIndex(material: Material) {
  const jobId = material.processing.index.job_id
  if (jobId === null) {
    actionErrors[material.id] = '暂未读取到问答索引作业，请刷新后重试。'
    return
  }
  retryingIndexId.value = material.id
  delete actionErrors[material.id]
  try {
    // 只重投索引作业；原文与定位不受影响，完成后列表按后端记录刷新为准备中或失败。
    await jobsApi.retry(jobId)
    await refresh()
    // 粘贴文本的识别状态与索引作业分开保存，重试后单独重读一次，避免显示旧的失败状态。
    await ensureRecognition(material)
  } catch (caught) {
    actionErrors[material.id] = `重试索引失败：${errorMessage(caught)}`
  } finally {
    retryingIndexId.value = null
  }
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
  delete recognitionByMaterial[id]
  delete recognitionErrors[id]
  delete actionErrors[id]
  jobChecked.delete(id)
  recognitionChecked.delete(id)
  if (sourceMaterial.value?.id === id) closeSource()
  // 保持生命周期面板打开，让用户看到“删除完成”的结果；列表在后台同步移除该资料。
  await refresh()
}

// 面板里的首个可交互元素：优先按钮／链接／表单控件，summary 也让键盘用户可以直接展开详情。
const PANEL_INTERACTIVE_SELECTOR = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), summary, [tabindex]:not([tabindex="-1"])'

function panelElement(instance: PanelInstance | null): HTMLElement | null {
  const element = instance?.$el
  return element instanceof HTMLElement ? element : null
}

/** 长列表里面板渲染在视口之外：打开后滚动进视口并聚焦首个可交互元素，避免看起来“按钮无效”。 */
async function focusPanel(panel: Ref<PanelInstance | null>) {
  await nextTick()
  const element = panelElement(panel.value)
  if (!element) return
  element.scrollIntoView({ block: 'center', behavior: 'smooth' })
  element.querySelector<HTMLElement>(PANEL_INTERACTIVE_SELECTOR)?.focus({ preventScroll: true })
}

/** 面板关闭后把焦点归还触发按钮；按钮已被列表刷新移除时不做任何事，不把焦点丢到文档开头。 */
function restoreFocus(trigger: HTMLElement | null) {
  if (trigger?.isConnected) trigger.focus()
}

function triggerElement(event?: Event): HTMLElement | null {
  return event?.currentTarget instanceof HTMLElement ? event.currentTarget : null
}

function openSource(material: Material, event?: Event) {
  sourceTrigger = triggerElement(event)
  sourceVersionNo.value = null
  sourceChunkId.value = null
  sourceMaterial.value = material
  void focusPanel(sourcePanel)
}

function closeSource() {
  sourceMaterial.value = null
  restoreFocus(sourceTrigger)
  sourceTrigger = null
}

function openLifecycle(material: Material, event?: Event) {
  lifecycleTrigger = triggerElement(event)
  lifecycleMaterial.value = material
  void focusPanel(lifecyclePanel)
}

function closeLifecycle() {
  lifecycleMaterial.value = null
  restoreFocus(lifecycleTrigger)
  lifecycleTrigger = null
}

/** 核对界面的「定位原文」带上引用所属版本与片段，避免把旧版本的摘录定位到最新版本上。 */
async function openReferencedSource() {
  const materialId = parsePositiveInt(route.query.material)
  if (materialId === null) return
  try {
    sourceMaterial.value = await materialsApi.get(materialId)
  } catch {
    // 资料已删除或不属于本空间时跳过定位，列表仍可正常查看。
    return
  }
  sourceVersionNo.value = parsePositiveInt(route.query.version)
  sourceChunkId.value = parsePositiveInt(route.query.chunk)
  // 由链接进入而非按钮触发：没有可归还焦点的触发按钮，只把面板滚动进视口。
  sourceTrigger = null
  void focusPanel(sourcePanel)
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
    if (caught instanceof ApiError && caught.kind === 'timeout') {
      // 上传超时也只是前端先放弃等待：逐文件结果未知，先刷新确认，不能直接重传造成重复建档。
      fallbackNotice.value = '上传超时：结果未知，请先刷新资料列表确认文件是否已经建档，不要直接重新上传相同文件。'
    }
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
    // 粘贴文本同步完成切分并直接返回片段，不创建解析作业，因此不会触发轮询。
    const result = await materialsApi.importText({
      title: textForm.title.trim(),
      content: textForm.content,
      publisher: textForm.publisher.trim() || null,
      published_at: textForm.published_at || null,
      semester_id: textForm.semester_id ? Number(textForm.semester_id) : null,
      course_id: textForm.course_id ? Number(textForm.course_id) : null,
      class_name: textForm.class_name.trim() || null,
    })
    successMessage.value = `已保存「${result.material.title}」原文并切分 ${result.chunks.length} 个段落；这只是保存，尚未准备问答或提取事项。请在下方资料条目点击「开始识别」。`
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
    if (caught instanceof ApiError && caught.kind === 'timeout') {
      // 超时只表示前端先放弃等待：后端可能仍在抓取或已经建档，不能据此断言正文未导入。
      fallbackNotice.value = `读取超时：结果未知，请先刷新资料列表确认「${webForm.title.trim()}」是否已经导入，不要直接重试以免重复建档。如确认未导入，可粘贴可访问的原文或上传已保存的文件；未覆盖内容不会被当作“没有要求”。`
    } else {
      // 非超时失败时后端不会保存网页快照；明确未覆盖范围，并提供粘贴/上传入口。
      fallbackNotice.value = `网页正文未导入：${importError.value}。可粘贴可访问的原文或上传已保存的文件；未覆盖内容不会被当作“没有要求”。`
    }
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
  pageHidden = document.visibilityState === 'hidden'
  document.addEventListener('visibilitychange', onVisibilityChange)
  await load()
  await openReferencedSource()
})

onBeforeUnmount(() => {
  document.removeEventListener('visibilitychange', onVisibilityChange)
  // 卸载后递增请求号：仍在途的列表响应不得写回已关闭的页面状态。
  unmounted = true
  listRequestId += 1
  stopPolling()
})
</script>

<template>
  <main class="page-content">
    <div class="page-heading page-heading-row">
      <div>
        <p class="eyebrow">资料库</p>
        <h1>资料</h1>
        <p class="intro">导入课程资料与通知，查看每一份资料的处理状态与未覆盖范围。</p>
        <p class="form-help">原文读取与问答索引分开显示，状态与未覆盖范围来自后端作业查询，不做本地推断；索引失败不会被显示为已准备，缺失内容也不会被当作“没有要求”。问答界面暂未提供入口，索引状态只表示检索数据是否已准备好。</p>
      </div>
      <button type="button" @click="importOpen = !importOpen">{{ importOpen ? '收起导入' : '导入资料' }}</button>
    </div>

    <ApiFeedback v-if="error" :message="error" :busy="loading" @retry="load" />
    <template v-else>
      <section v-if="importOpen" class="panel editor-panel">
        <div class="section-heading">
          <div><p class="eyebrow">导入资料</p><h2>粘贴文本、上传文件或读取网页</h2></div>
          <div class="segmented" role="group" aria-label="导入方式">
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
        <label>学期<select v-model="filters.semesterId" @change="filters.courseId = ''; reloadMaterials()"><option value="">全部学期</option><option v-for="semester in semesters" :key="semester.id" :value="semester.id">{{ semester.name }}{{ semester.archived ? '（已归档）' : '' }}</option></select></label>
        <label>课程<select v-model="filters.courseId" @change="reloadMaterials"><option value="">全部课程</option><option v-for="course in filteredCourses" :key="course.id" :value="course.id">{{ course.name }} · {{ course.class_name || '未分班' }}</option></select></label>
        <label>来源<select v-model="filters.sourceType" @change="reloadMaterials"><option value="">全部来源</option><option v-for="(label, value) in sourceTypeLabels" :key="value" :value="value">{{ label }}</option></select></label>
        <label>原文状态<select v-model="filters.status" @change="reloadMaterials"><option value="">全部原文状态</option><option v-for="(label, value) in statusLabels" :key="value" :value="value">{{ label }}</option></select></label>
        <label class="switch-field"><input v-model="filters.includeArchived" type="checkbox" @change="reloadMaterials" />包含已归档</label>
        <span class="count-badge">{{ materials.length }} 份</span>
      </section>

      <section class="panel">
        <div class="section-heading">
          <div><p class="eyebrow">资料列表</p><h2>处理状态</h2></div>
          <button type="button" class="text-button" :disabled="loading" @click="manualRefresh">刷新</button>
        </div>
        <p v-if="pollPaused" class="inline-notice" role="status">自动刷新已暂停，请手动刷新查看最新处理状态。</p>
        <p v-if="recognitionNotice" class="inline-notice" role="status">{{ recognitionNotice }}</p>
        <p v-if="loading" class="muted">正在加载资料…</p>
        <div v-else-if="materials.length" class="material-list">
          <article v-for="material in materials" :key="material.id" class="material-row">
            <div class="material-main">
              <div class="task-title-line">
                <h3>{{ material.title }}</h3>
                <span class="status-pill" data-axis="text" :data-status="material.processing.text_state">{{ textStateLabels[material.processing.text_state] }}</span>
                <span class="status-pill" data-axis="index" :data-status="material.processing.index.state">{{ indexStateLabels[material.processing.index.state] }}</span>
                <span v-if="material.archived" class="status-pill archived-pill">已归档</span>
              </div>
              <p>{{ sourceTypeLabels[material.source_type] }} · {{ scopeText(material) }} · 导入于 {{ formatDateTime(material.imported_at) }}</p>
              <p v-if="material.publisher">发布方：{{ material.publisher }}{{ material.published_at ? ` · ${material.published_at.slice(0, 10)}` : '' }}</p>
              <p v-if="jobErrors[material.id]" class="form-error" role="alert">{{ jobErrors[material.id] }}</p>
              <!-- 行级操作错误只提示该条资料；列表、筛选与导入表单保持可用，不切换整页错误。 -->
              <p v-if="actionErrors[material.id]" class="form-error" role="alert">{{ actionErrors[material.id] }}</p>
              <!-- 索引失败/部分/无法核对必须直接可见，不能只藏在展开详情里。 -->
              <p v-if="['failed', 'partial', 'unknown'].includes(material.processing.index.state)" class="index-warning">
                {{ material.processing.index.message }}
              </p>

              <!-- 粘贴文本：原文保存只是第一步；问答索引与事项提取必须显示真实状态并提供显式入口。 -->
              <div v-if="material.source_type === 'text'" class="recognition-box">
                <div class="task-title-line">
                  <strong>原文保存与识别分开</strong>
                  <span class="status-pill" :data-status="recognitionSummary(material).tone">{{ recognitionSummary(material).text }}</span>
                </div>
                <template v-if="recognitionFor(material)">
                  <p>问答索引：{{ recognitionIndexText(material) }} · 事项：{{ recognitionExtractionText(material) }}</p>
                  <p v-if="recognitionBlockedReason(material)" class="form-help">{{ recognitionBlockedReason(material) }}</p>
                  <p v-if="recognitionFor(material)?.extraction.state === 'done'" class="form-help">候选仍需人工核对，确认前不会成为正式任务；待核对页面暂未提供入口。</p>
                </template>
                <p v-else class="form-help">正在读取识别状态…</p>
                <p v-if="recognitionErrors[material.id]" class="form-error" role="alert">{{ recognitionErrors[material.id] }}</p>
                <div v-if="recognitionFor(material)" class="material-actions">
                  <button
                    v-if="recognitionCanStart(material)"
                    type="button"
                    class="button-secondary"
                    :disabled="recognizingId === material.id"
                    @click="startRecognition(material)"
                  >{{ recognizingId === material.id ? '正在识别…' : recognitionActionLabel(material) }}</button>
                  <span v-else-if="hasRecognitionInProgress(material)" class="muted small">识别进行中，页面会自动刷新</span>
                </div>
                <p class="form-help">只有点击「开始识别」才会调用模型；保存原文不会自动提取，也不会自动把候选确认为正式任务。问答与待核对页面当前暂未提供入口，识别状态只说明检索数据与候选是否已准备。</p>
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

              <!-- 两条状态轴的依据都来自后端处理记录；原文读取情况在这里展开，不隐藏可用原文。 -->
              <details class="processing-details">
                <summary>处理详情（原文读取 / 问答索引）</summary>
                <div class="processing-grid">
                  <div class="processing-axis">
                    <strong>原文读取</strong>
                    <p><span class="status-pill" :data-status="material.processing.text_state">{{ textStateLabels[material.processing.text_state] }}</span></p>
                    <p v-if="jobFor(material)?.scope">覆盖情况：{{ jobFor(material)?.scope }}</p>
                    <p v-if="jobFor(material)?.error_reason" class="form-error">{{ jobFor(material)?.error_reason }}</p>
                    <p v-if="material.processing.text_state === 'ready'" class="form-help">原文与定位已保存，可随时查看原文；这不代表问答索引已完成。</p>
                  </div>
                  <div class="processing-axis">
                    <strong>问答索引</strong>
                    <p><span class="status-pill" :data-status="material.processing.index.state">{{ indexStateLabels[material.processing.index.state] }}</span></p>
                    <p>{{ material.processing.index.message }}</p>
                    <p v-if="material.processing.index.detail">索引覆盖：{{ material.processing.index.detail }}</p>
                    <p v-if="material.processing.index.error" class="form-error">{{ material.processing.index.error }}</p>
                    <p v-if="material.processing.index.job_id !== null" class="form-help">
                      索引作业 #{{ material.processing.index.job_id }}{{ material.processing.index.retryable ? ' · 当前可重试' : ' · 当前不可重试' }}
                    </p>
                    <button
                      v-if="material.processing.index.retryable"
                      type="button"
                      class="button-secondary"
                      :disabled="retryingIndexId === material.id"
                      @click="retryIndex(material)"
                    >{{ retryingIndexId === material.id ? '正在重试索引…' : '重试索引' }}</button>
                  </div>
                </div>
                <p class="form-help">原文读取与问答索引分开记录；索引失败不会隐藏已读取的原文，也不会把索引显示为已准备。问答界面暂未提供入口，索引状态只说明检索数据是否已准备好。</p>
              </details>
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
              <span v-else-if="material.status === 'failed' && jobFor(material)" class="muted small">不可重试</span>
              <button
                v-if="material.processing.index.retryable"
                type="button"
                class="button-secondary"
                :disabled="retryingIndexId === material.id"
                @click="retryIndex(material)"
              >{{ retryingIndexId === material.id ? '正在重试索引…' : '重试索引' }}</button>
              <button type="button" class="text-button" @click="openSource(material, $event)">查看原文</button>
              <button type="button" class="text-button" @click="openLifecycle(material, $event)">归档 / 删除</button>
            </div>
          </article>
        </div>
        <div v-else class="compact-empty">{{ materials.length ? '没有符合当前筛选的资料。' : '还没有资料，先导入一份课程通知或大纲。' }}</div>
      </section>

      <MaterialSource
        v-if="sourceMaterial"
        :key="`${sourceMaterial.id}-${sourceVersionNo ?? 'latest'}-${sourceChunkId ?? 'first'}`"
        ref="sourcePanel"
        :material="sourceMaterial"
        :scope="scopeText(sourceMaterial)"
        :initial-version-no="sourceVersionNo"
        :initial-chunk-id="sourceChunkId"
        @close="closeSource"
      />

      <!-- 删除影响预览与确认令牌在子组件内完成；确认只覆盖预览展示过的范围，内容变化需重新预览。 -->
      <MaterialLifecycle
        v-if="lifecycleMaterial"
        :key="lifecycleMaterial.id"
        ref="lifecyclePanel"
        :material="lifecycleMaterial"
        @close="closeLifecycle"
        @archive-changed="onArchiveChanged"
        @deleted="onMaterialDeleted"
      />
    </template>
  </main>
</template>
