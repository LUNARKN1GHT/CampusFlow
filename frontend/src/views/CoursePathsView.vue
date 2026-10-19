<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'

import { semestersApi } from '../api/business'
import { errorMessage } from '../api/client'
import { pathsApi } from '../api/paths'
import type {
  AliasResolution,
  CourseAlias,
  CourseRelation,
  CourseStudyRecord,
  CourseStudyStatusSummary,
  CurriculumCourse,
  CurriculumPlan,
  LearnerProfile,
  Semester,
  StudyStatus,
  SwitchPreview,
} from '../api/types'

const relationLabels: Record<string, string> = {
  prerequisite: '强制先修',
  corequisite: '并修',
  recommended: '建议先学',
}
const statusLabels: Record<string, string> = {
  completed: '已修',
  in_progress: '在修',
  planned: '待修',
  unknown: '未知',
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

const selectedPlan = computed(() => plans.value.find((plan) => plan.id === selectedPlanId.value) ?? null)
const currentPlan = computed(() => profile.value?.current_plan ?? null)
const courseById = computed(() => new Map(courses.value.map((course) => [course.id, course])))
const semesterName = (id: number | null) =>
  id === null ? '未指定学期' : semesters.value.find((item) => item.id === id)?.name ?? `学期 #${id}`
const relationName = (id: number) => courseById.value.get(id)?.name ?? `课程 #${id}`

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

async function loadPlanData() {
  if (selectedPlanId.value === null) {
    courses.value = []
    relations.value = []
    aliases.value = []
    studyRecords.value = []
    studyStatus.value = null
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
    await loadStudy()
  } catch (caught) {
    actionError.value = errorMessage(caught)
  } finally {
    dataLoading.value = false
  }
}

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
</style>
