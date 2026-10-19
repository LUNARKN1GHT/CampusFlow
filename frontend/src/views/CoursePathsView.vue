<script setup lang="ts">
/*
 * 课程路径页：按培养方案版本维护课程、三类关系、别名与修读记录，并只读请求关系图、
 * 拓扑层次、下一步与学期试排。图、顺序、可学判断与学期建议共用同一份已确认正式关系，
 * 任何环、类型矛盾、缺项或未转换候选都会阻塞结论；页面只展示原因，不自行补出顺序。
 * 切换查看版本不影响档案中的当前方案，旧方案数据保留且不会迁移到新版本。
 */
import { computed, onMounted, reactive, ref, watch } from 'vue'

import { semestersApi } from '../api/business'
import { errorMessage } from '../api/client'
import { pathsApi } from '../api/paths'
import type {
  AliasResolution,
  CourseAlias,
  CourseGraph,
  CourseGraphEdge,
  CourseGraphNode,
  CourseGraphProblem,
  CourseOrder,
  CourseRelation,
  CourseStudyRecord,
  CourseStudyStatusSummary,
  CurriculumCourse,
  CurriculumPlan,
  LearnerProfile,
  NextCourses,
  Semester,
  SemesterPlan,
  StudyStatus,
  SwitchPreview,
} from '../api/types'

// 三类关系互斥；只有强制先修进入拓扑分层，并修只约束同修，建议先学仅提示。
const relationLabels: Record<string, string> = {
  prerequisite: '强制先修',
  corequisite: '并修',
  recommended: '建议先学',
}
const relationTypes = ['prerequisite', 'corequisite', 'recommended'] as const
const statusLabels: Record<string, string> = {
  completed: '已修',
  in_progress: '在修',
  planned: '待修',
  unknown: '未知',
}
// 后端问题代码的中文映射；未收录的代码原样显示，避免前端猜测含义。
const problemCodeLabels: Record<string, string> = {
  prerequisite_cycle: '强制先修成环',
  relation_conflict: '关系类型矛盾',
  relation_candidate_incomplete: '关系候选信息不完整',
  relation_candidate_unmatched: '课程无法唯一匹配',
  relation_candidate_conflict: '候选与正式关系冲突',
  relation_candidate_pending: '关系候选待核对',
}
const termLabels: Record<string, string> = {
  fall: '秋',
  spring: '春',
  summer: '夏',
  winter: '冬',
}

const loading = ref(true)
const error = ref('')
const actionError = ref('')
const notice = ref('')
const profile = ref<LearnerProfile | null>(null)
const plans = ref<CurriculumPlan[]>([])
const selectedPlanId = ref<number | null>(null)
const activeTab = ref<'courses' | 'relations' | 'aliases' | 'study'>('courses')

const courses = ref<CurriculumCourse[]>([])
const relations = ref<CourseRelation[]>([])
const aliases = ref<CourseAlias[]>([])
const studyRecords = ref<CourseStudyRecord[]>([])
const studyStatus = ref<CourseStudyStatusSummary | null>(null)
const semesters = ref<Semester[]>([])
const dataLoading = ref(false)
const studyUnavailable = ref(false)

const preview = ref<SwitchPreview | null>(null)
const previewLoading = ref(false)

const planForm = reactive({
  title: '',
  major: '',
  grade_year: 2024,
  version_label: '',
  credit_limit_per_term: null as number | null,
})

const courseForm = reactive({
  id: null as number | null,
  name: '',
  code: '',
  credits: '',
  offered_terms: [] as string[],
})
const courseSaving = ref(false)

const relationForm = reactive({
  from_course_id: null as number | null,
  to_course_id: null as number | null,
  relation_type: 'prerequisite',
  condition_text: '',
})
const relationSaving = ref(false)

const aliasForm = reactive({
  curriculum_course_id: null as number | null,
  alias: '',
  class_name: '',
  semester_id: null as number | null,
})
const aliasSaving = ref(false)
const resolveName = ref('')
const resolveClass = ref('')
const resolveSemester = ref<number | null>(null)
const resolution = ref<AliasResolution | null>(null)
const resolving = ref(false)

const studyForm = reactive({
  curriculum_course_id: null as number | null,
  semester_id: null as number | null,
  status: 'completed' as StudyStatus,
  grade: '',
  credits_earned: '',
  note: '',
})
const studySaving = ref(false)

const graph = ref<CourseGraph | null>(null)
const graphLoading = ref(false)
const graphError = ref('')
const order = ref<CourseOrder | null>(null)
const orderLoading = ref(false)
const orderError = ref('')
const nextCourses = ref<NextCourses | null>(null)
const nextLoading = ref(false)
const nextError = ref('')
const selectedNodeId = ref<number | null>(null)
const selectedEdgeId = ref<number | null>(null)

const semesterForm = reactive({
  credit_limit: '',
  start_season: 'fall' as 'fall' | 'spring',
  max_terms: '',
})
const semesterPlan = ref<SemesterPlan | null>(null)
const semesterLoading = ref(false)
const semesterError = ref('')

const selectedPlan = computed(() => plans.value.find((plan) => plan.id === selectedPlanId.value) ?? null)
const currentPlan = computed(() => profile.value?.current_plan ?? null)
const courseById = computed(() => new Map(courses.value.map((course) => [course.id, course])))
const semesterName = (id: number | null) =>
  id === null ? '未指定学期' : semesters.value.find((item) => item.id === id)?.name ?? `学期 #${id}`
const relationName = (id: number) => courseById.value.get(id)?.name ?? `课程 #${id}`

const graphNodeById = computed(
  () => new Map((graph.value?.nodes ?? []).map((node) => [node.id, node])),
)
function courseName(id: number): string {
  return graphNodeById.value.get(id)?.name ?? courseById.value.get(id)?.name ?? `课程 #${id}`
}

const selectedGraphNode = computed<CourseGraphNode | null>(
  () => graph.value?.nodes.find((node) => node.id === selectedNodeId.value) ?? null,
)
const selectedGraphEdge = computed<CourseGraphEdge | null>(
  () => graph.value?.edges.find((edge) => edge.id === selectedEdgeId.value) ?? null,
)
// 成环高亮只取后端返回的环路径上的边与课程，不在前端另行推断环。
const cycleRelationIds = computed(
  () => new Set((graph.value?.cycles ?? []).flatMap((problem) => problem.relation_ids)),
)
const cycleCourseIds = computed(
  () => new Set((graph.value?.cycles ?? []).flatMap((problem) => [...problem.path, ...problem.course_ids])),
)

// 固定四组与 /graph 的 cycles／conflicts／missing／pending 字段一一对应：
// missing 含候选缺字段与课名无法唯一匹配，pending 含待核对与类型冲突的未转换候选；
// 分组只影响展示，是否阻塞仍以后端 complete 为准。
const graphProblemGroups = computed(() => {
  const current = graph.value
  if (!current) return []
  return [
    { key: 'cycles', label: '强制先修环', tone: 'danger', items: current.cycles },
    { key: 'conflicts', label: '关系类型矛盾', tone: 'danger', items: current.conflicts },
    { key: 'missing', label: '缺项与无法匹配', tone: 'warn', items: current.missing },
    { key: 'pending', label: '待核对候选', tone: 'info', items: current.pending },
  ]
})

// 节点尺寸与间距固定，SVG 宽高、连线锚点和点击热区都由这些常量推导。
const GRAPH_NODE_WIDTH = 172
const GRAPH_NODE_HEIGHT = 54
const GRAPH_COLUMN_GAP = 72
const GRAPH_ROW_GAP = 18
const GRAPH_PADDING = 18

type GraphLayout = {
  nodes: Array<{ node: CourseGraphNode; x: number; y: number; column: number; row: number }>
  edges: Array<{ edge: CourseGraphEdge; path: string; self: boolean; cycle: boolean }>
  width: number
  height: number
  columns: number
}

const graphLayout = computed<GraphLayout>(() => {
  const current = graph.value
  if (!current || !current.nodes.length) {
    return { nodes: [], edges: [], width: 0, height: 0, columns: 0 }
  }
  // 布局要画出全部课程，因此自行按强制先修做一次 Kahn 分层，不复用 /order：
  // 后者在存在问题时返回空层次，直接使用会让有问题的图无节点可渲染。
  const ids = current.nodes.map((node) => node.id)
  const idSet = new Set(ids)
  const indegree = new Map(ids.map((id) => [id, 0]))
  const adjacency = new Map<number, number[]>()
  current.edges.forEach((edge) => {
    if (edge.relation_type !== 'prerequisite') return
    if (!idSet.has(edge.from_course_id) || !idSet.has(edge.to_course_id)) return
    const targets = adjacency.get(edge.from_course_id) ?? []
    targets.push(edge.to_course_id)
    adjacency.set(edge.from_course_id, targets)
    indegree.set(edge.to_course_id, (indegree.get(edge.to_course_id) ?? 0) + 1)
  })
  const levelById = new Map<number, number>()
  let frontier = ids.filter((id) => (indegree.get(id) ?? 0) === 0)
  let depth = 0
  while (frontier.length) {
    frontier.forEach((id) => levelById.set(id, depth))
    const next: number[] = []
    frontier.forEach((id) => {
      for (const target of adjacency.get(id) ?? []) {
        const degree = (indegree.get(target) ?? 0) - 1
        indegree.set(target, degree)
        if (degree === 0) next.push(target)
      }
    })
    frontier = Array.from(new Set(next))
    depth += 1
  }
  // 成环及环下游的课程没有稳定层，统一放到最后一列，不伪造层次。
  const remaining = current.nodes.filter((node) => !levelById.has(node.id))
  const finalColumn = remaining.length ? Math.max(0, depth) : Math.max(0, depth - 1)
  remaining.forEach((node) => levelById.set(node.id, finalColumn))
  const groups = new Map<number, CourseGraphNode[]>()
  current.nodes.forEach((node) => {
    const column = levelById.get(node.id) ?? 0
    const group = groups.get(column) ?? []
    group.push(node)
    groups.set(column, group)
  })
  const columns = Array.from(groups.entries())
    .sort(([left], [right]) => left - right)
    .map(([, nodes]) => [...nodes].sort((left, right) => left.name.localeCompare(right.name, 'zh')))
  const maxRows = Math.max(...columns.map((nodes) => nodes.length))
  const width =
    columns.length * GRAPH_NODE_WIDTH +
    Math.max(0, columns.length - 1) * GRAPH_COLUMN_GAP +
    GRAPH_PADDING * 2
  const height =
    maxRows * GRAPH_NODE_HEIGHT +
    Math.max(0, maxRows - 1) * GRAPH_ROW_GAP +
    GRAPH_PADDING * 2
  // 列号来自强制先修链上的 Kahn 分层，行号按中文课名排序；
  // 位置只由节点集合推导，重复渲染结果稳定。
  const positions = new Map<number, { x: number; y: number; column: number; row: number }>()
  const layoutNodes: GraphLayout['nodes'] = []
  columns.forEach((nodes, column) => {
    nodes.forEach((node, row) => {
      const position = {
        x: GRAPH_PADDING + column * (GRAPH_NODE_WIDTH + GRAPH_COLUMN_GAP),
        y: GRAPH_PADDING + row * (GRAPH_NODE_HEIGHT + GRAPH_ROW_GAP),
        column,
        row,
      }
      positions.set(node.id, position)
      layoutNodes.push({ node, ...position })
    })
  })
  const layoutEdges = current.edges.flatMap((edge) => {
    const source = positions.get(edge.from_course_id)
    const target = positions.get(edge.to_course_id)
    if (!source || !target) return []
    const sx = source.x + GRAPH_NODE_WIDTH
    const sy = source.y + GRAPH_NODE_HEIGHT / 2
    const tx = target.x
    const ty = target.y + GRAPH_NODE_HEIGHT / 2
    // 连线从源节点右侧中点连到目标节点左侧中点；同列或回边改用绕行曲率只为
    // 避免穿过节点，箭头方向仍表示“来源课程 → 受限课程”。
    let path: string
    if (edge.from_course_id === edge.to_course_id) {
      // 自环必须可见：从节点右侧绕出再回到自身。
      path = `M ${sx} ${sy - 9} C ${sx + 52} ${sy - 34}, ${sx + 52} ${sy + 34}, ${sx} ${sy + 9}`
    } else if (target.column > source.column) {
      const handle = Math.max(36, (tx - sx) / 2)
      path = `M ${sx} ${sy} C ${sx + handle} ${sy}, ${tx - handle} ${ty}, ${tx} ${ty}`
    } else if (target.column === source.column) {
      path = `M ${sx} ${sy} C ${sx + 58} ${sy}, ${tx + 58} ${ty}, ${tx} ${ty}`
    } else {
      path = `M ${sx} ${sy} C ${sx + 76} ${sy - 66}, ${tx - 76} ${ty + 66}, ${tx} ${ty}`
    }
    return [{ edge, path, self: edge.from_course_id === edge.to_course_id, cycle: cycleRelationIds.value.has(edge.id) }]
  })
  return { nodes: layoutNodes, edges: layoutEdges, width, height, columns: columns.length }
})

// next 接口把已完成课程标为不可进入，两处列表都排除，避免出现在“待满足条件”里；
// 没有修读记录时状态为 unknown，按未完成处理，不推断为已修或未修。
const nextAvailable = computed(
  () =>
    nextCourses.value?.courses.filter(
      (course) => course.available && course.study_status !== 'completed',
    ) ?? [],
)
const nextBlocked = computed(
  () =>
    nextCourses.value?.courses.filter(
      (course) => !course.available && course.study_status !== 'completed',
    ) ?? [],
)

// 节点与连线详情互斥，仅保留一个选中项；再次点击同一项取消选中。
function toggleGraphNode(nodeId: number) {
  selectedEdgeId.value = null
  selectedNodeId.value = selectedNodeId.value === nodeId ? null : nodeId
}

function toggleGraphEdge(edgeId: number) {
  selectedNodeId.value = null
  selectedEdgeId.value = selectedEdgeId.value === edgeId ? null : edgeId
}

function problemCodeLabel(code: string): string {
  return problemCodeLabels[code] ?? code
}

// 环问题优先展示 path（含回到起点的完整路径），其余问题退回 course_ids。
function problemCourseNames(problem: CourseGraphProblem): string {
  const ids = problem.path.length ? problem.path : problem.course_ids
  return ids.map(courseName).join(' → ')
}

// 节点宽度固定，超长课名截断；完整名称由模板里的 <title> 提供悬浮提示。
function shortNodeName(name: string): string {
  return name.length > 12 ? `${name.slice(0, 12)}…` : name
}

// 默认查看档案中的当前方案；没有当前方案时只选中第一个版本供查看，
// 真正写入档案仍需用户在切换预览中确认。
async function load() {
  loading.value = true
  error.value = ''
  try {
    const [loadedProfile, loadedPlans, loadedSemesters] = await Promise.all([
      pathsApi.profile(),
      pathsApi.listPlans(),
      semestersApi.list(true),
    ])
    profile.value = loadedProfile
    plans.value = loadedPlans
    semesters.value = loadedSemesters
    selectedPlanId.value =
      loadedProfile.current_plan?.id ?? loadedPlans[0]?.id ?? null
    if (selectedPlan.value) {
      Object.assign(planForm, {
        title: selectedPlan.value.title,
        major: selectedPlan.value.major,
        grade_year: selectedPlan.value.grade_year,
        version_label: selectedPlan.value.version_label,
        credit_limit_per_term: selectedPlan.value.credit_limit_per_term,
      })
    }
  } catch (caught) {
    error.value = errorMessage(caught)
  } finally {
    loading.value = false
  }
}

// 方案切换后所有列表与分析都重新拉取；没有选中方案时清空并重置，
// 避免上一个方案的图或顺序残留在页面上。
async function loadPlanData() {
  if (selectedPlanId.value === null) {
    courses.value = []
    relations.value = []
    aliases.value = []
    studyRecords.value = []
    studyStatus.value = null
    resetAnalysis()
    return
  }
  dataLoading.value = true
  actionError.value = ''
  try {
    const [loadedCourses, loadedRelations, loadedAliases] = await Promise.all([
      pathsApi.listCourses(selectedPlanId.value),
      pathsApi.listRelations(selectedPlanId.value),
      pathsApi.listAliases(selectedPlanId.value),
    ])
    courses.value = loadedCourses
    relations.value = loadedRelations
    aliases.value = loadedAliases
    await Promise.all([loadStudy(), loadAnalysis()])
  } catch (caught) {
    actionError.value = errorMessage(caught)
  } finally {
    dataLoading.value = false
  }
}

function resetAnalysis() {
  graph.value = null
  graphError.value = ''
  order.value = null
  orderError.value = ''
  nextCourses.value = null
  nextError.value = ''
  selectedNodeId.value = null
  selectedEdgeId.value = null
  semesterPlan.value = null
  semesterError.value = ''
}

async function loadAnalysis() {
  const planId = selectedPlanId.value
  selectedNodeId.value = null
  selectedEdgeId.value = null
  semesterPlan.value = null
  semesterError.value = ''
  if (planId === null) {
    resetAnalysis()
    return
  }
  graphLoading.value = true
  orderLoading.value = true
  nextLoading.value = true
  // 三个只读分析并行且各自容错，任一失败只清空对应区块；
  // 返回时若已切换方案则丢弃结果，防止旧响应覆盖新方案的数据。
  const [graphResult, orderResult, nextResult] = await Promise.allSettled([
    pathsApi.graph(planId),
    pathsApi.order(planId),
    pathsApi.nextCourses(planId),
  ])
  if (selectedPlanId.value === planId) {
    if (graphResult.status === 'fulfilled') {
      graph.value = graphResult.value
      graphError.value = ''
    } else {
      graph.value = null
      graphError.value = errorMessage(graphResult.reason)
    }
    if (orderResult.status === 'fulfilled') {
      order.value = orderResult.value
      orderError.value = ''
    } else {
      order.value = null
      orderError.value = errorMessage(orderResult.reason)
    }
    if (nextResult.status === 'fulfilled') {
      nextCourses.value = nextResult.value
      nextError.value = ''
    } else {
      nextCourses.value = null
      nextError.value = errorMessage(nextResult.reason)
    }
    graphLoading.value = false
    orderLoading.value = false
    nextLoading.value = false
  }
}

// 留空表示交给后端：学分上限回落到方案设置，最多学期数自动；
// 页面不把空字符串当作 0，也不在本地预判可排性。
async function generateSemesterPlan() {
  if (selectedPlanId.value === null) return
  semesterLoading.value = true
  semesterError.value = ''
  try {
    semesterPlan.value = await pathsApi.semesterPlan(selectedPlanId.value, {
      credit_limit: semesterForm.credit_limit.trim() === '' ? null : Number(semesterForm.credit_limit),
      start_season: semesterForm.start_season,
      max_terms: semesterForm.max_terms.trim() === '' ? null : Number(semesterForm.max_terms),
    })
  } catch (caught) {
    semesterPlan.value = null
    semesterError.value = errorMessage(caught)
  } finally {
    semesterLoading.value = false
  }
}

// 修读记录与汇总任一失败即整体标记不可用，避免把空数据展示成“未修”或“状态未知”。
async function loadStudy() {
  if (selectedPlanId.value === null) return
  studyUnavailable.value = false
  try {
    const [records, statuses] = await Promise.all([
      pathsApi.listStudyRecords(selectedPlanId.value),
      pathsApi.studyStatus(selectedPlanId.value),
    ])
    studyRecords.value = records
    studyStatus.value = statuses
  } catch {
    studyUnavailable.value = true
    studyRecords.value = []
    studyStatus.value = null
  }
}

// 查看版本与档案当前方案分离：仅当二者不同时读取切换影响预览，
// 预览里的计数属于当前方案，切换不会删除旧方案数据。
async function selectPlan(planId: number) {
  selectedPlanId.value = planId
  preview.value = null
  const plan = plans.value.find((item) => item.id === planId)
  if (plan) {
    Object.assign(planForm, {
      title: plan.title,
      major: plan.major,
      grade_year: plan.grade_year,
      version_label: plan.version_label,
      credit_limit_per_term: plan.credit_limit_per_term,
    })
  }
  if (profile.value?.curriculum_plan_id !== planId) {
    previewLoading.value = true
    try {
      preview.value = await pathsApi.switchPreview(planId)
    } catch (caught) {
      actionError.value = errorMessage(caught)
    } finally {
      previewLoading.value = false
    }
  }
}

// 写档案后按新方案重载；旧方案数据保留但不再进入当前路径，不做迁移。
async function applyCurrentPlan() {
  if (selectedPlanId.value === null || profile.value === null) return
  actionError.value = ''
  try {
    profile.value = await pathsApi.updateProfile({
      major: profile.value.major,
      grade_year: profile.value.grade_year,
      curriculum_plan_id: selectedPlanId.value,
    })
    notice.value = '当前适用方案已更新；旧方案数据保留，但不再显示在当前路径中。'
    preview.value = null
    await loadPlanData()
  } catch (caught) {
    actionError.value = errorMessage(caught)
  }
}

async function createPlan() {
  actionError.value = ''
  notice.value = ''
  try {
    const created = await pathsApi.createPlan({
      title: planForm.title,
      major: planForm.major,
      grade_year: Number(planForm.grade_year),
      version_label: planForm.version_label,
      credit_limit_per_term: planForm.credit_limit_per_term,
    })
    plans.value.push(created)
    await selectPlan(created.id)
    notice.value = '培养方案版本已创建；请确认无误后设为当前方案。'
  } catch (caught) {
    actionError.value = errorMessage(caught)
  }
}

function editCourse(course?: CurriculumCourse) {
  Object.assign(courseForm, {
    id: course?.id ?? null,
    name: course?.name ?? '',
    code: course?.code ?? '',
    credits: course?.credits === null || course?.credits === undefined ? '' : String(course.credits),
    offered_terms: [...(course?.offered_terms ?? [])],
  })
}

async function saveCourse() {
  if (selectedPlanId.value === null) return
  actionError.value = ''
  courseSaving.value = true
  const payload = {
    name: courseForm.name,
    code: courseForm.code || null,
    credits: courseForm.credits === '' ? null : Number(courseForm.credits),
    offered_terms: courseForm.offered_terms,
  }
  try {
    if (courseForm.id === null) {
      await pathsApi.createCourse(selectedPlanId.value, payload)
      notice.value = '课程已加入方案。'
    } else {
      await pathsApi.updateCourse(selectedPlanId.value, courseForm.id, payload)
      notice.value = '课程已更新。'
    }
    await loadPlanData()
    editCourse()
  } catch (caught) {
    actionError.value = errorMessage(caught)
  } finally {
    courseSaving.value = false
  }
}

async function removeCourse(course: CurriculumCourse) {
  if (selectedPlanId.value === null) return
  if (!confirm(`删除课程“${course.name}”？被关系引用的课程需要先删除关系。`)) return
  actionError.value = ''
  try {
    await pathsApi.deleteCourse(selectedPlanId.value, course.id)
    await loadPlanData()
  } catch (caught) {
    actionError.value = errorMessage(caught)
  }
}

function toggleTerm(term: string) {
  const index = courseForm.offered_terms.indexOf(term)
  if (index >= 0) courseForm.offered_terms.splice(index, 1)
  else courseForm.offered_terms.push(term)
}

async function addRelation() {
  if (
    selectedPlanId.value === null ||
    relationForm.from_course_id === null ||
    relationForm.to_course_id === null
  ) return
  actionError.value = ''
  relationSaving.value = true
  try {
    await pathsApi.createRelation(selectedPlanId.value, {
      from_course_id: relationForm.from_course_id,
      to_course_id: relationForm.to_course_id,
      relation_type: relationForm.relation_type,
      condition_text: relationForm.condition_text || null,
    })
    relationForm.condition_text = ''
    notice.value = '关系已保存；建议先学只作提示，强制先修才参与排序。'
    await loadPlanData()
  } catch (caught) {
    actionError.value = errorMessage(caught)
  } finally {
    relationSaving.value = false
  }
}

async function removeRelation(relation: CourseRelation) {
  if (selectedPlanId.value === null) return
  actionError.value = ''
  try {
    await pathsApi.deleteRelation(selectedPlanId.value, relation.id)
    await loadPlanData()
  } catch (caught) {
    actionError.value = errorMessage(caught)
  }
}

async function addAlias() {
  if (selectedPlanId.value === null || aliasForm.curriculum_course_id === null) return
  actionError.value = ''
  aliasSaving.value = true
  try {
    await pathsApi.createAlias(selectedPlanId.value, {
      curriculum_course_id: aliasForm.curriculum_course_id,
      alias: aliasForm.alias,
      class_name: aliasForm.class_name || null,
      semester_id: aliasForm.semester_id,
    })
    aliasForm.alias = ''
    notice.value = '别名已保存；有范围限定的别名只在范围匹配时参与自动解析。'
    await loadPlanData()
  } catch (caught) {
    actionError.value = errorMessage(caught)
  } finally {
    aliasSaving.value = false
  }
}

async function removeAlias(alias: CourseAlias) {
  if (selectedPlanId.value === null) return
  actionError.value = ''
  try {
    await pathsApi.deleteAlias(selectedPlanId.value, alias.id)
    await loadPlanData()
  } catch (caught) {
    actionError.value = errorMessage(caught)
  }
}

// 名称解析只做严格匹配：空白归一、拉丁字母不区分大小写，正式课名优先，
// 别名仅在学期／教学班范围满足时参与；多命中返回歧义，不自动绑定或合并课程。
async function resolve() {
  if (selectedPlanId.value === null || !resolveName.value.trim()) return
  resolving.value = true
  actionError.value = ''
  try {
    resolution.value = await pathsApi.resolveAlias(selectedPlanId.value, resolveName.value, {
      className: resolveClass.value || null,
      semesterId: resolveSemester.value,
    })
  } catch (caught) {
    actionError.value = errorMessage(caught)
  } finally {
    resolving.value = false
  }
}

async function addStudyRecord() {
  if (selectedPlanId.value === null || studyForm.curriculum_course_id === null) return
  actionError.value = ''
  studySaving.value = true
  try {
    await pathsApi.createStudyRecord(selectedPlanId.value, {
      curriculum_course_id: studyForm.curriculum_course_id,
      semester_id: studyForm.semester_id,
      status: studyForm.status,
      grade: studyForm.grade || null,
      credits_earned: studyForm.credits_earned === '' ? null : Number(studyForm.credits_earned),
      note: studyForm.note || null,
    })
    studyForm.grade = ''
    studyForm.credits_earned = ''
    studyForm.note = ''
    notice.value = '修读记录已保存；成绩与已获学分只采用你填写的值。'
    await loadStudy()
  } catch (caught) {
    actionError.value = errorMessage(caught)
  } finally {
    studySaving.value = false
  }
}

async function removeStudyRecord(record: CourseStudyRecord) {
  if (selectedPlanId.value === null) return
  actionError.value = ''
  try {
    await pathsApi.deleteStudyRecord(selectedPlanId.value, record.id)
    await loadStudy()
  } catch (caught) {
    actionError.value = errorMessage(caught)
  }
}

// 方案切换由 watch 全量重载；挂载时先等 load() 选出默认版本再加载，避免用空方案请求分析。
watch(selectedPlanId, loadPlanData)
onMounted(async () => {
  await load()
  await loadPlanData()
})
</script>

<template>
  <main class="page-content">
    <div class="page-heading">
      <p class="eyebrow">课程关系与修读路径</p>
      <h1>课程路径</h1>
      <p class="intro">
        按适用培养方案维护课程、三类关系、别名与个人修读状态；当前方案版本决定哪些关系参与路径分析。
      </p>
      <p class="form-help">
        这里只分析依赖关系，不构成毕业资格或选课审核；缺失学分、开课学期或课程时会明确列出。
      </p>
    </div>

    <div v-if="error" class="status-banner failed" role="alert">
      <div><strong>加载失败</strong><p>{{ error }}</p></div>
    </div>
    <p v-else-if="loading" class="muted">正在加载培养方案…</p>

    <template v-else>
      <section class="panel current-plan-panel">
        <div class="section-heading">
          <div>
            <p class="eyebrow">当前适用方案</p>
            <h2>{{ currentPlan ? `${currentPlan.major} · ${currentPlan.grade_year} 级 · ${currentPlan.version_label}` : '尚未选择方案' }}</h2>
          </div>
          <span class="status-pill">{{ currentPlan?.title ?? '待补充' }}</span>
        </div>
        <div v-if="profile?.missing_fields.length" class="status-banner failed">
          <div>
            <strong>个人方案信息待补充</strong>
            <p>缺少：{{ profile.missing_fields.join('、') }}。缺失时不推断年级或版本。</p>
          </div>
        </div>
        <dl class="plan-facts">
          <div><dt>每学期学分上限</dt><dd>{{ currentPlan?.credit_limit_per_term ?? '未知' }}</dd></div>
          <div><dt>课程数</dt><dd>{{ courses.length }}</dd></div>
          <div><dt>关系数</dt><dd>{{ relations.length }}</dd></div>
          <div><dt>修读记录</dt><dd>{{ studyRecords.length }}</dd></div>
        </dl>
      </section>

      <div class="two-column-layout">
        <section class="panel">
          <div class="section-heading">
            <div><p class="eyebrow">方案版本</p><h2>选择或新建</h2></div>
            <span class="count-badge">{{ plans.length }} 个版本</span>
          </div>
          <div class="plan-list">
            <button
              v-for="plan in plans"
              :key="plan.id"
              type="button"
              :class="['plan-choice', { selected: plan.id === selectedPlanId }]"
              @click="selectPlan(plan.id)"
            >
              <strong>{{ plan.version_label }}</strong>
              <span>{{ plan.major }} · {{ plan.grade_year }} 级</span>
              <small>{{ plan.id === currentPlan?.id ? '当前方案' : '点击查看' }}</small>
            </button>
          </div>
          <p v-if="previewLoading" class="muted">正在读取切换影响…</p>
          <div v-if="preview" class="switch-preview">
            <strong>切换到 {{ preview.target_plan.version_label }}</strong>
            <p>{{ preview.note }}</p>
            <p>当前方案有 {{ preview.course_count }} 门课程、{{ preview.relation_count }} 条关系和 {{ preview.study_record_count }} 条修读记录；切换后不再显示。</p>
            <button type="button" @click="applyCurrentPlan">设为当前方案</button>
          </div>
        </section>

        <section class="panel">
          <div class="section-heading"><div><p class="eyebrow">新建版本</p><h2>培养方案</h2></div></div>
          <form class="stack-form" @submit.prevent="createPlan">
            <label>方案名称<input v-model.trim="planForm.title" required maxlength="200" /></label>
            <label>专业<input v-model.trim="planForm.major" required maxlength="100" /></label>
            <div class="form-grid">
              <label>入学年级<input v-model.number="planForm.grade_year" type="number" min="1900" max="2100" required /></label>
              <label>版本标签<input v-model.trim="planForm.version_label" required maxlength="100" placeholder="例如：2024 版" /></label>
            </div>
            <label>每学期学分上限（未知留空）<input v-model.number="planForm.credit_limit_per_term" type="number" min="1" /></label>
            <button type="submit">创建方案版本</button>
          </form>
        </section>
      </div>

      <p v-if="actionError" class="form-error" role="alert">{{ actionError }}</p>
      <div v-if="notice" class="status-banner success"><div><strong>{{ notice }}</strong></div></div>

      <section class="panel">
        <div class="tab-row" role="tablist">
          <button type="button" :class="{ active: activeTab === 'courses' }" @click="activeTab = 'courses'">课程目录</button>
          <button type="button" :class="{ active: activeTab === 'relations' }" @click="activeTab = 'relations'">课程关系</button>
          <button type="button" :class="{ active: activeTab === 'aliases' }" @click="activeTab = 'aliases'">别名匹配</button>
          <button type="button" :class="{ active: activeTab === 'study' }" @click="activeTab = 'study'">修读状态</button>
        </div>
        <p v-if="dataLoading" class="muted">正在加载方案数据…</p>

        <template v-else-if="activeTab === 'courses'">
          <div class="section-heading">
            <div><p class="eyebrow">方案课程</p><h2>课程与学分</h2></div>
            <span class="count-badge">{{ courses.length }} 门</span>
          </div>
          <div class="data-table">
            <table>
              <thead><tr><th>课程</th><th>代码</th><th>学分</th><th>开课季节</th><th>来源</th><th></th></tr></thead>
              <tbody>
                <tr v-for="course in courses" :key="course.id">
                  <td><strong>{{ course.name }}</strong></td>
                  <td>{{ course.code ?? '未填写' }}</td>
                  <td>{{ course.credits ?? '未知' }}</td>
                  <td>{{ course.offered_terms.length ? course.offered_terms.map((term) => termLabels[term]).join('、') : '未知' }}</td>
                  <td>
                    <RouterLink v-if="course.source_candidate_id" :to="{ path: '/review', query: { candidate: course.source_candidate_id } }">候选 #{{ course.source_candidate_id }}</RouterLink>
                    <span v-else>手动</span>
                  </td>
                  <td class="row-actions">
                    <button type="button" class="text-button" @click="editCourse(course)">编辑</button>
                    <button type="button" class="text-button danger" @click="removeCourse(course)">删除</button>
                  </td>
                </tr>
              </tbody>
            </table>
            <p v-if="!courses.length" class="compact-empty">方案还没有课程。可从资料提取候选，或手动添加。</p>
          </div>
          <form class="stack-form inline-editor" @submit.prevent="saveCourse">
            <div class="section-heading"><div><p class="eyebrow">{{ courseForm.id === null ? '新增' : '编辑' }}</p><h3>{{ courseForm.name || '课程' }}</h3></div></div>
            <div class="form-grid">
              <label>课程名称<input v-model.trim="courseForm.name" required maxlength="200" /></label>
              <label>课程代码<input v-model.trim="courseForm.code" maxlength="50" /></label>
            </div>
            <div class="form-grid">
              <label>学分（未知留空）<input v-model.trim="courseForm.credits" type="number" min="0" max="100" step="0.5" /></label>
              <div class="term-choice">
                <span>开课季节</span>
                <label v-for="(label, term) in termLabels" :key="term" class="check-line">
                  <input type="checkbox" :checked="courseForm.offered_terms.includes(term)" @change="toggleTerm(term)" /> {{ label }}
                </label>
              </div>
            </div>
            <div class="row-actions">
              <button type="submit" :disabled="courseSaving">{{ courseSaving ? '保存中…' : '保存课程' }}</button>
              <button v-if="courseForm.id !== null" type="button" class="button-secondary" @click="editCourse()">取消编辑</button>
            </div>
          </form>
        </template>

        <template v-else-if="activeTab === 'relations'">
          <div class="section-heading">
            <div><p class="eyebrow">三类关系</p><h2>先修、并修与建议先学</h2></div>
            <span class="count-badge">{{ relations.length }} 条</span>
          </div>
          <div class="legend-row">
            <span v-for="(label, type) in relationLabels" :key="type" :class="['legend-chip', type]">{{ label }}</span>
          </div>
          <div class="data-table">
            <table>
              <thead><tr><th>类型</th><th>方向</th><th>条件原文</th><th>来源</th><th></th></tr></thead>
              <tbody>
                <tr v-for="relation in relations" :key="relation.id">
                  <td><span :class="['legend-chip', relation.relation_type]">{{ relationLabels[relation.relation_type] }}</span></td>
                  <td>{{ relationName(relation.from_course_id) }} → {{ relationName(relation.to_course_id) }}</td>
                  <td>{{ relation.condition_text ?? '未填写' }}</td>
                  <td>
                    <RouterLink v-if="relation.source_candidate_id" :to="{ path: '/review', query: { candidate: relation.source_candidate_id } }">候选 #{{ relation.source_candidate_id }}</RouterLink>
                    <span v-else>手动</span>
                  </td>
                  <td class="row-actions"><button type="button" class="text-button danger" @click="removeRelation(relation)">删除</button></td>
                </tr>
              </tbody>
            </table>
            <p v-if="!relations.length" class="compact-empty">还没有课程关系。建议先学不会阻塞修读。</p>
          </div>
          <form class="stack-form inline-editor" @submit.prevent="addRelation">
            <div class="form-grid">
              <label>来源课程（先修/建议）
                <select v-model.number="relationForm.from_course_id" required>
                  <option :value="null" disabled>请选择</option>
                  <option v-for="course in courses" :key="course.id" :value="course.id">{{ course.name }}</option>
                </select>
              </label>
              <label>受限课程
                <select v-model.number="relationForm.to_course_id" required>
                  <option :value="null" disabled>请选择</option>
                  <option v-for="course in courses" :key="course.id" :value="course.id">{{ course.name }}</option>
                </select>
              </label>
            </div>
            <div class="form-grid">
              <label>关系类型
                <select v-model="relationForm.relation_type">
                  <option v-for="(label, type) in relationLabels" :key="type" :value="type">{{ label }}</option>
                </select>
              </label>
              <label>条件原文（可选）<input v-model.trim="relationForm.condition_text" maxlength="1000" /></label>
            </div>
            <button type="submit" :disabled="relationSaving">{{ relationSaving ? '保存中…' : '添加关系' }}</button>
          </form>
        </template>

        <template v-else-if="activeTab === 'aliases'">
          <div class="section-heading">
            <div><p class="eyebrow">名称匹配</p><h2>课程别名</h2></div>
            <span class="count-badge">{{ aliases.length }} 个</span>
          </div>
          <div class="data-table">
            <table>
              <thead><tr><th>别名</th><th>对应课程</th><th>教学班</th><th>学期</th><th></th></tr></thead>
              <tbody>
                <tr v-for="alias in aliases" :key="alias.id">
                  <td><strong>{{ alias.alias }}</strong></td>
                  <td>{{ relationName(alias.curriculum_course_id) }}</td>
                  <td>{{ alias.class_name ?? '不限' }}</td>
                  <td>{{ alias.semester_id ? semesterName(alias.semester_id) : '不限' }}</td>
                  <td class="row-actions"><button type="button" class="text-button danger" @click="removeAlias(alias)">删除</button></td>
                </tr>
              </tbody>
            </table>
            <p v-if="!aliases.length" class="compact-empty">还没有别名。别名只用于名称匹配，不合并真实课程。</p>
          </div>
          <form class="stack-form inline-editor" @submit.prevent="addAlias">
            <div class="form-grid">
              <label>对应课程
                <select v-model.number="aliasForm.curriculum_course_id" required>
                  <option :value="null" disabled>请选择</option>
                  <option v-for="course in courses" :key="course.id" :value="course.id">{{ course.name }}</option>
                </select>
              </label>
              <label>别名<input v-model.trim="aliasForm.alias" required maxlength="200" /></label>
            </div>
            <div class="form-grid">
              <label>教学班（可选）<input v-model.trim="aliasForm.class_name" maxlength="100" /></label>
              <label>学期（可选）
                <select v-model="aliasForm.semester_id">
                  <option :value="null">不限</option>
                  <option v-for="semester in semesters" :key="semester.id" :value="semester.id">{{ semester.name }}</option>
                </select>
              </label>
            </div>
            <button type="submit" :disabled="aliasSaving">{{ aliasSaving ? '保存中…' : '添加别名' }}</button>
          </form>
          <form class="stack-form inline-editor" @submit.prevent="resolve">
            <div class="section-heading"><div><p class="eyebrow">只读检查</p><h3>解析课程名称</h3></div></div>
            <div class="form-grid">
              <label>名称<input v-model.trim="resolveName" required placeholder="正式课名或别名" /></label>
              <label>教学班<input v-model.trim="resolveClass" maxlength="100" /></label>
            </div>
            <label>学期
              <select v-model="resolveSemester">
                <option :value="null">不指定</option>
                <option v-for="semester in semesters" :key="semester.id" :value="semester.id">{{ semester.name }}</option>
              </select>
            </label>
            <button type="submit" class="button-secondary" :disabled="resolving">{{ resolving ? '解析中…' : '解析名称' }}</button>
            <p v-if="resolution" :class="['status-banner', resolution.status === 'matched' ? 'success' : 'failed']">
              <span>
                <strong>{{ resolution.status === 'matched' ? '唯一匹配' : resolution.status === 'ambiguous' ? '存在歧义' : '未找到' }}</strong>
                <p>{{ resolution.note }}</p>
                <span v-if="resolution.matches.length">候选：{{ resolution.matches.map((course) => course.name).join('、') }}</span>
              </span>
            </p>
          </form>
        </template>

        <!-- 前三个页签都已显式判断，剩余分支即“修读状态”。 -->
        <template v-else>
          <div class="section-heading">
            <div><p class="eyebrow">个人修读</p><h2>已修、在修与待修</h2></div>
            <span class="count-badge">{{ studyRecords.length }} 条记录</span>
          </div>
          <p v-if="studyUnavailable" class="status-banner failed">
            <span><strong>修读状态暂不可用</strong><p>后端修读记录接口尚未接入；当前不会伪造任何状态。</p></span>
          </p>
          <template v-else>
            <div class="data-table">
              <table>
                <thead><tr><th>课程</th><th>状态</th><th>学期</th><th>成绩</th><th>已获学分</th><th>备注</th><th></th></tr></thead>
                <tbody>
                  <tr v-for="record in studyRecords" :key="record.id">
                    <td>{{ relationName(record.curriculum_course_id) }}</td>
                    <td><span class="status-pill">{{ statusLabels[record.status] }}</span></td>
                    <td>{{ semesterName(record.semester_id) }}</td>
                    <td>{{ record.grade ?? '未填写' }}</td>
                    <td>{{ record.credits_earned ?? '未填写' }}</td>
                    <td>{{ record.note ?? '—' }}</td>
                    <td class="row-actions"><button type="button" class="text-button danger" @click="removeStudyRecord(record)">删除</button></td>
                  </tr>
                </tbody>
              </table>
              <p v-if="!studyRecords.length" class="compact-empty">还没有修读记录；没有记录表示状态未知，不表示未修。</p>
            </div>
            <div class="section-heading">
              <div><p class="eyebrow">汇总</p><h3>课程状态</h3></div>
              <span v-if="studyStatus" class="count-badge">
                已修 {{ studyStatus.completed_count }} · 在修 {{ studyStatus.in_progress_count }} · 待修 {{ studyStatus.planned_count }} · 未知 {{ studyStatus.unknown_count }}
              </span>
            </div>
            <p v-if="studyStatus?.note" class="form-help">{{ studyStatus.note }}</p>
            <div class="data-table">
              <table>
                <thead><tr><th>课程</th><th>汇总状态</th><th>记录数</th><th>重复修读</th><th>完成学期</th></tr></thead>
                <tbody>
                  <tr v-for="item in studyStatus?.courses ?? []" :key="item.curriculum_course_id">
                    <td>{{ item.course_name }}</td>
                    <td><span class="status-pill">{{ statusLabels[item.status] }}</span></td>
                    <td>{{ item.record_count }}</td>
                    <td>{{ item.repeat_record_count || '—' }}</td>
                    <td>{{ item.completed_semester_ids.length ? item.completed_semester_ids.map(semesterName).join('、') : '—' }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <form class="stack-form inline-editor" @submit.prevent="addStudyRecord">
              <div class="form-grid">
                <label>课程
                  <select v-model.number="studyForm.curriculum_course_id" required>
                    <option :value="null" disabled>请选择</option>
                    <option v-for="course in courses" :key="course.id" :value="course.id">{{ course.name }}</option>
                  </select>
                </label>
                <label>状态
                  <select v-model="studyForm.status">
                    <option value="completed">已修</option>
                    <option value="in_progress">在修</option>
                    <option value="planned">待修</option>
                  </select>
                </label>
              </div>
              <div class="form-grid">
                <label>学期
                  <select v-model="studyForm.semester_id">
                    <option :value="null">未指定</option>
                    <option v-for="semester in semesters" :key="semester.id" :value="semester.id">{{ semester.name }}</option>
                  </select>
                </label>
                <label>成绩（可选）<input v-model.trim="studyForm.grade" maxlength="20" /></label>
              </div>
              <div class="form-grid">
                <label>已获学分（可选，不自动按课程学分推算）<input v-model.trim="studyForm.credits_earned" type="number" min="0" step="0.5" /></label>
                <label>备注<input v-model.trim="studyForm.note" maxlength="500" /></label>
              </div>
              <button type="submit" :disabled="studySaving">{{ studySaving ? '保存中…' : '添加修读记录' }}</button>
            </form>
          </template>
        </template>
      </section>

      <section class="panel graph-panel">
        <div class="section-heading">
          <div><p class="eyebrow">C015 课程关系图</p><h2>依赖图与来源证据</h2></div>
          <span v-if="graph" :class="['status-pill', graph.complete ? 'graph-complete' : 'graph-incomplete']">
            {{ graph.complete ? '图完整' : '图不完整' }}
          </span>
        </div>
        <p class="form-help">
          只绘制当前方案内已确认的正式关系；节点按强制先修深度分列。点击课程节点或连线查看字段、条件与原文证据。
        </p>
        <p v-if="graphLoading" class="muted">正在加载课程关系图…</p>
        <div v-else-if="graphError" class="status-banner failed" role="alert">
          <div><strong>课程关系图加载失败</strong><p>{{ graphError }}</p></div>
        </div>
        <template v-else-if="graph">
          <div :class="['status-banner', graph.complete ? 'success' : 'failed']">
            <div>
              <strong>{{ graph.complete ? '关系图完整' : '图不完整，不能宣称依赖关系已完整' }}</strong>
              <p v-if="graph.complete">接口未报告环、缺项、矛盾或未转换候选；仍只代表当前方案内已确认的关系。</p>
              <p v-else>环、缺项、矛盾或待核对候选的原因列在下方问题清单中；未核对前不生成完整结论。</p>
            </div>
          </div>
          <ul v-if="graph.limitations.length" class="limitations-list">
            <li v-for="(limitation, index) in graph.limitations" :key="`graph-limit-${index}`">{{ limitation }}</li>
          </ul>
          <div class="legend-row graph-legend">
            <span class="legend-chip prerequisite">强制先修 · 实线箭头</span>
            <span class="legend-chip corequisite">并修 · 点划线</span>
            <span class="legend-chip recommended">建议先学 · 虚线</span>
            <span class="legend-chip cycle">成环 · 高亮加粗</span>
          </div>
          <p v-if="!graph.nodes.length" class="compact-empty">当前方案还没有课程，无法绘制关系图。</p>
          <template v-else>
            <div class="graph-scroll">
              <svg
                :width="graphLayout.width"
                :height="graphLayout.height"
                :viewBox="`0 0 ${graphLayout.width} ${graphLayout.height}`"
                class="graph-svg"
                role="img"
                aria-label="课程关系图"
              >
                <defs>
                  <marker
                    v-for="type in relationTypes"
                    :id="`graph-arrow-${type}`"
                    :key="`marker-${type}`"
                    markerWidth="9"
                    markerHeight="9"
                    refX="8"
                    refY="4.5"
                    orient="auto-start-reverse"
                  >
                    <path d="M0,0 L9,4.5 L0,9 z" :class="`marker-${type}`" />
                  </marker>
                </defs>
                <g>
                  <g
                    v-for="item in graphLayout.edges"
                    :key="`edge-${item.edge.id}`"
                    :class="['graph-edge', { cycle: item.cycle, selected: selectedEdgeId === item.edge.id }]"
                    @click.stop="toggleGraphEdge(item.edge.id)"
                  >
                    <path :d="item.path" class="edge-hit" />
                    <path
                      :d="item.path"
                      :class="[
                        'edge-line',
                        `edge-${item.edge.relation_type}`,
                        {
                          selected: selectedEdgeId === item.edge.id,
                          'edge-self': item.self,
                          'edge-cycle': item.cycle,
                        },
                      ]"
                      :marker-end="`url(#graph-arrow-${item.edge.relation_type})`"
                    />
                  </g>
                </g>
                <g
                  v-for="item in graphLayout.nodes"
                  :key="`node-${item.node.id}`"
                  :class="[
                    'graph-node',
                    {
                      cycle: cycleCourseIds.has(item.node.id),
                      selected: selectedNodeId === item.node.id,
                    },
                  ]"
                  :transform="`translate(${item.x}, ${item.y})`"
                  role="button"
                  tabindex="0"
                  @click.stop="toggleGraphNode(item.node.id)"
                  @keydown.enter.prevent="toggleGraphNode(item.node.id)"
                  @keydown.space.prevent="toggleGraphNode(item.node.id)"
                >
                  <title>{{ item.node.name }}</title>
                  <rect :width="GRAPH_NODE_WIDTH" :height="GRAPH_NODE_HEIGHT" rx="12" />
                  <text x="12" y="23" class="node-name">{{ shortNodeName(item.node.name) }}</text>
                  <text x="12" y="41" class="node-meta">
                    {{ item.node.code ?? '未填写代码' }} · {{ item.node.credits ?? '?' }} 学分 · {{ statusLabels[item.node.study_status] ?? item.node.study_status }}
                  </text>
                </g>
              </svg>
            </div>
            <div v-if="selectedGraphNode" class="graph-detail">
              <div class="section-heading">
                <div><p class="eyebrow">课程详情</p><h3>{{ selectedGraphNode.name }}</h3></div>
                <button type="button" class="text-button" @click="selectedNodeId = null">关闭</button>
              </div>
              <dl class="detail-grid">
                <div><dt>课程代码</dt><dd>{{ selectedGraphNode.code ?? '未填写' }}</dd></div>
                <div><dt>学分</dt><dd>{{ selectedGraphNode.credits ?? '未知' }}</dd></div>
                <div><dt>开课季节</dt><dd>{{ selectedGraphNode.offered_terms.length ? selectedGraphNode.offered_terms.map((term) => termLabels[term] ?? term).join('、') : '未知' }}</dd></div>
                <div><dt>修读状态</dt><dd>{{ statusLabels[selectedGraphNode.study_status] ?? selectedGraphNode.study_status }}</dd></div>
                <div><dt>修读记录</dt><dd>{{ selectedGraphNode.record_count }} 条</dd></div>
              </dl>
            </div>
            <div v-else-if="selectedGraphEdge" class="graph-detail">
              <div class="section-heading">
                <div>
                  <p class="eyebrow">关系详情</p>
                  <h3>{{ courseName(selectedGraphEdge.from_course_id) }} → {{ courseName(selectedGraphEdge.to_course_id) }}</h3>
                </div>
                <button type="button" class="text-button" @click="selectedEdgeId = null">关闭</button>
              </div>
              <dl class="detail-grid">
                <div><dt>关系类型</dt><dd><span :class="['legend-chip', selectedGraphEdge.relation_type]">{{ relationLabels[selectedGraphEdge.relation_type] }}</span></dd></div>
                <div><dt>条件原文</dt><dd>{{ selectedGraphEdge.condition_text ?? '未填写' }}</dd></div>
                <div>
                  <dt>来源候选</dt>
                  <dd>
                    <RouterLink v-if="selectedGraphEdge.source_candidate_id" :to="{ path: '/review', query: { candidate: selectedGraphEdge.source_candidate_id } }">候选 #{{ selectedGraphEdge.source_candidate_id }}</RouterLink>
                    <span v-else>手动录入</span>
                  </dd>
                </div>
              </dl>
              <!-- 证据停留在字段／片段／版本／资料编号，不在本页跳转资料库；关系候选另给出核对入口。 -->
              <div class="evidence-list">
                <p class="eyebrow">原文证据</p>
                <p v-if="!selectedGraphEdge.evidence.length" class="compact-empty">该关系没有可展示的原文证据；不代表关系已被原文确认。</p>
                <ul v-else>
                  <li v-for="(evidence, index) in selectedGraphEdge.evidence" :key="`evidence-${index}`">
                    <blockquote>{{ evidence.quote }}</blockquote>
                    <p class="form-help">
                      字段 {{ evidence.field }} · 片段 #{{ evidence.chunk_id }} · 版本 #{{ evidence.version_id }}
                      <template v-if="evidence.material_id"> · 资料 #{{ evidence.material_id }}</template>
                    </p>
                  </li>
                </ul>
              </div>
            </div>
          </template>
          <!-- 标题标注“始终可见”：四组固定渲染，空组显示“无”，不让某类问题从页面消失。 -->
          <div class="problem-section">
            <div class="section-heading">
              <div><p class="eyebrow">问题清单</p><h3>环、矛盾、缺项与待核对</h3></div>
              <span class="count-badge">始终可见</span>
            </div>
            <p v-if="!graph.complete" class="form-error">图不完整：以下问题未核对前，不能把当前依赖关系当作完整约束。</p>
            <p v-else class="form-help">接口未报告环、缺项、矛盾或待核对关系；空清单不等于依赖绝对正确。</p>
            <div v-for="group in graphProblemGroups" :key="group.key" :class="['problem-group', group.tone]">
              <div class="problem-group-heading">
                <strong>{{ group.label }}</strong>
                <span class="count-badge">{{ group.items.length }} 条</span>
              </div>
              <p v-if="!group.items.length" class="compact-empty">无</p>
              <ul v-else>
                <li v-for="(problem, index) in group.items" :key="`${group.key}-${index}`">
                  <p class="problem-title"><span class="problem-code">{{ problem.code }}</span>{{ problemCodeLabel(problem.code) }}</p>
                  <p>{{ problem.message }}</p>
                  <p v-if="problemCourseNames(problem)" class="form-help">涉及课程：{{ problemCourseNames(problem) }}</p>
                  <p v-if="problem.relation_ids.length" class="form-help">相关关系：{{ problem.relation_ids.map((id) => `#${id}`).join('、') }}</p>
                  <p v-if="problem.candidate_ids.length" class="form-help">
                    来源候选：
                    <RouterLink v-for="candidateId in problem.candidate_ids" :key="candidateId" :to="{ path: '/review', query: { candidate: candidateId } }">#{{ candidateId }}</RouterLink>
                  </p>
                </li>
              </ul>
            </div>
          </div>
        </template>
      </section>

      <div class="two-column-layout">
        <section class="panel">
          <div class="section-heading">
            <div><p class="eyebrow">C016 修读顺序</p><h2>强制先修层次</h2></div>
            <span v-if="order" class="count-badge">{{ order.levels.length }} 层</span>
          </div>
          <p class="form-help">只按强制先修分层；并修与建议先学不参与层次计算。</p>
          <p v-if="orderLoading" class="muted">正在计算修读顺序…</p>
          <div v-else-if="orderError" class="status-banner failed" role="alert">
            <div><strong>修读顺序加载失败</strong><p>{{ orderError }}</p></div>
          </div>
          <template v-else-if="order">
            <div :class="['status-banner', order.blocked ? 'failed' : 'success']">
              <div>
                <strong>{{ order.blocked ? '未生成完整顺序' : '已生成依赖层次' }}</strong>
                <p v-if="order.blocked">存在未核对问题，后端没有输出层次；下方列出阻塞原因，不伪造顺序。</p>
                <p v-else-if="!order.levels.length">当前方案没有可排序的课程。</p>
                <p v-else>层次只表示强制先修先后，不代表每学期必须按层修读。</p>
              </div>
            </div>
            <ul v-if="order.problems.length" class="problem-list">
              <li v-for="(problem, index) in order.problems" :key="`order-problem-${index}`">
                <p class="problem-title"><span class="problem-code">{{ problem.code }}</span>{{ problemCodeLabel(problem.code) }}</p>
                <p>{{ problem.message }}</p>
                <p v-if="problem.path.length" class="form-help">成环路径：{{ problemCourseNames(problem) }}</p>
                <p v-else-if="problem.course_ids.length" class="form-help">涉及课程：{{ problemCourseNames(problem) }}</p>
                <p v-if="problem.relation_ids.length" class="form-help">相关关系：{{ problem.relation_ids.map((id) => `#${id}`).join('、') }}</p>
              </li>
            </ul>
            <!-- 阻塞时后端 levels 为空；这里不渲染占位层次，避免把“未输出”误读为“无课”。 -->
            <div v-if="order.levels.length" class="level-grid">
              <div v-for="(level, index) in order.levels" :key="`level-${index}`" class="level-card">
                <p class="eyebrow">第 {{ index + 1 }} 层</p>
                <ul>
                  <li v-for="courseId in level" :key="courseId">{{ courseName(courseId) }}</li>
                </ul>
              </div>
            </div>
            <p v-if="order.alternatives" class="status-banner warn">
              <span><strong>存在可替换层次</strong><p>同一层内课程没有相互强制先修，可调整先后；层次内顺序不是硬性要求。</p></span>
            </p>
            <ul v-if="order.limitations.length" class="limitations-list">
              <li v-for="(limitation, index) in order.limitations" :key="`order-limit-${index}`">{{ limitation }}</li>
            </ul>
          </template>
        </section>

        <section class="panel">
          <div class="section-heading">
            <div><p class="eyebrow">C016 下一步</p><h2>可学与待满足条件</h2></div>
            <span v-if="nextCourses" class="count-badge">{{ nextAvailable.length }} 门可进入</span>
          </div>
          <p v-if="nextLoading" class="muted">正在判断下一步可学课程…</p>
          <div v-else-if="nextError" class="status-banner failed" role="alert">
            <div><strong>下一步课程加载失败</strong><p>{{ nextError }}</p></div>
          </div>
          <template v-else-if="nextCourses">
            <div v-if="nextCourses.blocked" class="status-banner warn">
              <div><strong>依赖图不完整</strong><p>以下判断只基于已确认关系；未核对约束可能改变结果。</p></div>
            </div>
            <p v-if="!nextCourses.courses.length" class="compact-empty">当前方案没有课程，无法判断下一步。</p>
            <template v-else>
              <h3 class="subheading">可进入的课程</h3>
              <p v-if="!nextAvailable.length" class="compact-empty">现有资料中没有可进入的课程；这不等于没有课程可修。</p>
              <ul v-else class="next-list">
                <li v-for="course in nextAvailable" :key="`next-${course.course_id}`">
                  <div class="next-title">
                    <strong>{{ course.name }}</strong>
                    <span class="status-pill">{{ statusLabels[course.study_status] ?? course.study_status }}</span>
                  </div>
                  <p v-if="course.recommended.length" class="form-help">建议先学（不阻塞）：{{ course.recommended.join('；') }}</p>
                </li>
              </ul>
              <template v-if="nextBlocked.length">
                <h3 class="subheading">暂未满足条件</h3>
                <ul class="next-list">
                  <li v-for="course in nextBlocked" :key="`blocked-${course.course_id}`">
                    <div class="next-title">
                      <strong>{{ course.name }}</strong>
                      <span class="status-pill">{{ statusLabels[course.study_status] ?? course.study_status }}</span>
                    </div>
                    <ul v-if="course.unmet.length" class="condition-list">
                      <li v-for="(item, index) in course.unmet" :key="`unmet-${index}`">{{ item }}</li>
                    </ul>
                    <p v-if="course.recommended.length" class="form-help">建议先学（不阻塞）：{{ course.recommended.join('；') }}</p>
                  </li>
                </ul>
              </template>
              <p class="form-help">建议先学不阻塞修读；只有强制先修与并修条件会影响可进入判断。</p>
            </template>
            <ul v-if="nextCourses.limitations.length" class="limitations-list">
              <li v-for="(limitation, index) in nextCourses.limitations" :key="`next-limit-${index}`">{{ limitation }}</li>
            </ul>
          </template>
        </section>
      </div>

      <section class="panel">
        <div class="section-heading">
          <div><p class="eyebrow">C016 学期辅助</p><h2>按学分上限与开课季节试排</h2></div>
          <span class="status-pill">仅作参考</span>
        </div>
        <p class="form-help">
          学期建议只依据强制先修、已知开课季节和学分上限，不构成选课或毕业资格审核（graduation_audit=false）。
        </p>
        <form class="stack-form semester-form" @submit.prevent="generateSemesterPlan">
          <div class="form-grid">
            <label>
              每学期学分上限（留空使用方案值：{{ selectedPlan?.credit_limit_per_term ?? '未知' }}）
              <input v-model.trim="semesterForm.credit_limit" type="number" min="1" max="60" placeholder="留空使用方案值" />
            </label>
            <label>
              起始季节
              <select v-model="semesterForm.start_season">
                <option value="fall">秋季</option>
                <option value="spring">春季</option>
              </select>
            </label>
            <label>
              最多学期数（可选）
              <input v-model.trim="semesterForm.max_terms" type="number" min="1" max="16" placeholder="留空自动" />
            </label>
          </div>
          <button type="submit" :disabled="semesterLoading || selectedPlanId === null">
            {{ semesterLoading ? '生成中…' : '生成学期建议' }}
          </button>
        </form>
        <p v-if="semesterError" class="form-error" role="alert">{{ semesterError }}</p>
        <template v-if="semesterPlan">
          <div :class="['status-banner', semesterPlan.blocked ? 'failed' : 'success']">
            <div>
              <strong>{{ semesterPlan.blocked ? '未生成学期建议' : '学期建议已生成' }}</strong>
              <p v-if="semesterPlan.blocked">当前关系图存在未核对问题，后端未输出学期方案；原因见下方限制说明。</p>
              <p v-else>学分上限：{{ semesterPlan.credit_limit ?? '未知（未检查学分负荷上限）' }}。</p>
            </div>
          </div>
          <!-- 有结果时始终展示审核边界；学分上限未知等降级说明由后端 limitations／assumptions 给出。 -->
          <p class="status-banner warn">
            <span><strong>不构成选课或毕业资格审核</strong><p>graduation_audit=false；请以学校正式培养方案和选课系统为准。</p></span>
          </p>
          <div v-if="semesterPlan.terms.length" class="term-grid">
            <div v-for="term in semesterPlan.terms" :key="`term-${term.index}`" class="term-card">
              <div class="term-heading">
                <strong>第 {{ term.index + 1 }} 学期 · {{ termLabels[term.season] ?? term.season }}季</strong>
                <span class="count-badge">已知 {{ term.credits_known }} 学分</span>
              </div>
              <ul>
                <li v-for="courseId in term.course_ids" :key="courseId">{{ courseName(courseId) }}</li>
              </ul>
              <ul v-if="term.warnings.length" class="condition-list">
                <li v-for="(warning, index) in term.warnings" :key="`term-warning-${index}`">{{ warning }}</li>
              </ul>
            </div>
          </div>
          <p v-else class="compact-empty">没有可展示的学期建议。</p>
          <template v-if="semesterPlan.unplaced.length">
            <h3 class="subheading">未排入的课程</h3>
            <ul class="unplaced-list">
              <li v-for="item in semesterPlan.unplaced" :key="`unplaced-${item.course_id}`">
                <strong>{{ item.name }}</strong>
                <span>{{ item.reason }}</span>
              </li>
            </ul>
          </template>
          <div v-if="semesterPlan.limitations.length" class="analysis-notes">
            <h3 class="subheading">限制说明</h3>
            <ul class="limitations-list">
              <li v-for="(limitation, index) in semesterPlan.limitations" :key="`semester-limit-${index}`">{{ limitation }}</li>
            </ul>
          </div>
          <div v-if="semesterPlan.assumptions.length" class="analysis-notes">
            <h3 class="subheading">使用的假设</h3>
            <ul class="limitations-list">
              <li v-for="(assumption, index) in semesterPlan.assumptions" :key="`semester-assumption-${index}`">{{ assumption }}</li>
            </ul>
          </div>
        </template>
      </section>
    </template>
  </main>
</template>

<style scoped>
.current-plan-panel { margin-bottom: 18px; }
.plan-facts { display: flex; flex-wrap: wrap; gap: 18px; margin: 12px 0 0; }
.plan-facts div { display: grid; gap: 2px; }
.plan-facts dt { color: var(--muted); font-size: 12px; }
.plan-facts dd { margin: 0; font-weight: 800; }
.plan-list { display: grid; gap: 8px; }
.plan-choice { display: grid; gap: 2px; padding: 10px 12px; border: 1px solid var(--line); border-radius: 10px; color: var(--ink); background: var(--paper-strong); text-align: left; cursor: pointer; }
.plan-choice small { color: var(--muted); }
.plan-choice.selected { border-color: var(--brand-border); background: var(--brand-selected); }
.switch-preview { display: grid; gap: 8px; margin-top: 12px; padding: 12px; border: 1px solid var(--brand-border); border-radius: 10px; background: var(--brand-soft); }
.switch-preview p { margin: 0; font-size: 13px; }
.tab-row { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 16px; }
.tab-row button { border: 1px solid var(--line-strong); border-radius: 999px; background: var(--paper-strong); color: var(--muted); padding: 7px 13px; }
.tab-row button.active { color: var(--brand); border-color: var(--brand-border); background: var(--brand-selected); font-weight: 800; }
.data-table { overflow-x: auto; margin: 10px 0 16px; }
.data-table table { width: 100%; border-collapse: collapse; font-size: 13px; }
.data-table th, .data-table td { padding: 9px 8px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }
.data-table th { color: var(--muted); font-size: 11px; letter-spacing: .04em; text-transform: uppercase; }
.row-actions { display: flex; gap: 8px; white-space: nowrap; }
.text-button.danger { color: var(--danger-strong-ink); }
.legend-row { display: flex; flex-wrap: wrap; gap: 8px; margin: 6px 0; }
.legend-chip { display: inline-block; padding: 3px 9px; border-radius: 999px; font-size: 11px; font-weight: 800; border: 1px solid var(--line-strong); }
.legend-chip.prerequisite { color: var(--danger-strong-ink); background: var(--danger-bg-soft); border-color: transparent; }
.legend-chip.corequisite { color: var(--warn-strong-ink); background: var(--accent-soft); border-color: transparent; }
.legend-chip.recommended { color: var(--info-ink); background: var(--info-bg); border-color: transparent; }
.inline-editor { margin-top: 18px; padding-top: 16px; border-top: 1px dashed var(--line-strong); }
.term-choice { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; color: var(--muted); font-size: 13px; font-weight: 700; }
.term-choice .check-line { display: flex; align-items: center; gap: 4px; }
.term-choice .check-line input { width: auto; }
.status-banner.warn { color: var(--warn-strong-ink); background: var(--warn-bg); }
.status-banner.warn p { color: var(--warn-ink-soft); }

.graph-panel { margin-top: 18px; }
.graph-complete { color: var(--brand); background: var(--brand-soft); }
.graph-incomplete { color: var(--danger-strong-ink); background: var(--danger-bg-soft); }
.graph-legend { margin: 10px 0 12px; }
.legend-chip.cycle { color: var(--danger-strong-ink); background: var(--danger-bg); border-color: var(--danger-border); }
.graph-scroll { overflow-x: auto; padding: 8px; border: 1px solid var(--line); border-radius: 12px; background: var(--surface-muted); }
.graph-svg { display: block; min-width: 100%; }
.graph-edge { cursor: pointer; }
.edge-hit { fill: none; stroke: transparent; stroke-width: 14; }
.edge-line { fill: none; stroke: var(--line-strong); stroke-width: 1.8; }
.edge-prerequisite { stroke: var(--danger-strong-ink); stroke-width: 2.1; }
.edge-corequisite { stroke: var(--warn-strong-ink); stroke-width: 2.1; stroke-dasharray: 9 4 2 4; }
.edge-recommended { stroke: var(--info-ink); stroke-width: 1.8; stroke-dasharray: 5 5; }
.graph-edge.cycle .edge-line, .edge-cycle { stroke-width: 3.2; }
.graph-edge.selected .edge-line { stroke-width: 3.6; }
.marker-prerequisite { fill: var(--danger-strong-ink); }
.marker-corequisite { fill: var(--warn-strong-ink); }
.marker-recommended { fill: var(--info-ink); }
.graph-node { cursor: pointer; outline: none; }
.graph-node rect { fill: var(--paper-strong); stroke: var(--line-strong); stroke-width: 1.5; }
.graph-node:hover rect, .graph-node:focus-visible rect { stroke: var(--brand-border); }
.graph-node.selected rect { stroke: var(--brand); stroke-width: 2.6; }
.graph-node.cycle rect { stroke: var(--danger-strong-ink); stroke-dasharray: 6 3; }
.node-name { fill: var(--ink); font-size: 13px; font-weight: 800; }
.node-meta { fill: var(--muted); font-size: 10.5px; }
.graph-node.cycle .node-name { fill: var(--danger-strong-ink); }
.graph-detail { display: grid; gap: 8px; margin-top: 14px; padding: 14px; border: 1px solid var(--brand-border); border-radius: 12px; background: var(--brand-selected); }
.graph-detail .section-heading { margin-bottom: 0; }
.detail-grid { display: flex; flex-wrap: wrap; gap: 18px; margin: 0; }
.detail-grid div { display: grid; gap: 2px; }
.detail-grid dt { color: var(--muted); font-size: 11px; }
.detail-grid dd { margin: 0; font-weight: 700; }
.evidence-list ul, .problem-list, .problem-group ul, .next-list, .unplaced-list { list-style: none; margin: 0; padding: 0; display: grid; gap: 10px; }
.evidence-list blockquote { margin: 0 0 6px; padding: 9px 12px; border-left: 3px solid var(--brand-border); border-radius: 0 8px 8px 0; background: var(--paper-strong); white-space: pre-wrap; }
.problem-section { margin-top: 18px; padding-top: 16px; border-top: 1px dashed var(--line-strong); }
.problem-group { margin-top: 10px; padding: 10px 12px; border: 1px solid var(--line); border-radius: 10px; background: var(--surface-muted); }
.problem-group.danger { border-color: var(--danger-border); background: var(--danger-bg); }
.problem-group.warn { border-color: var(--warn-border); background: var(--warn-bg); }
.problem-group.info { border-color: var(--line-strong); background: var(--info-bg); }
.problem-group-heading { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 8px; }
.problem-group p, .problem-list p { margin: 3px 0; font-size: 12.5px; }
.problem-title { display: flex; align-items: center; gap: 8px; font-size: 13px; font-weight: 800; }
.problem-code { padding: 2px 6px; border-radius: 6px; background: var(--surface-soft); color: var(--muted-strong); font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 10px; font-weight: 700; }
.level-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 10px; margin: 12px 0; }
.level-card { padding: 10px 12px; border: 1px solid var(--line); border-radius: 10px; background: var(--surface-muted); }
.level-card ul { margin: 0; padding-left: 18px; display: grid; gap: 3px; }
.level-card .eyebrow { margin-bottom: 4px; }
.subheading { margin-top: 14px; }
.next-list > li { padding: 10px 12px; border: 1px solid var(--line); border-radius: 10px; background: var(--surface-muted); }
.next-title { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.condition-list { margin: 6px 0 0; padding-left: 18px; color: var(--muted-strong); font-size: 12.5px; display: grid; gap: 2px; }
.semester-form { margin: 12px 0; }
.term-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(230px, 1fr)); gap: 10px; margin: 12px 0; }
.term-card { display: grid; gap: 6px; padding: 12px; border: 1px solid var(--line); border-radius: 10px; background: var(--surface-muted); }
.term-heading { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.term-card ul { margin: 0; padding-left: 18px; display: grid; gap: 3px; }
.unplaced-list { margin: 8px 0; }
.unplaced-list li { display: flex; justify-content: space-between; gap: 12px; padding: 8px 10px; border: 1px solid var(--warn-border); border-radius: 8px; background: var(--warn-bg); }
.unplaced-list span { color: var(--warn-strong-ink); text-align: right; }
.analysis-notes { margin-top: 12px; }
.limitations-list { margin: 8px 0 0; padding-left: 18px; color: var(--muted-strong); font-size: 12.5px; display: grid; gap: 4px; }
</style>
