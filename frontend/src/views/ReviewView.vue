<script setup lang="ts">
/*
 * 待核对列表：按核对状态、事项类型、学期／课程、来源资料与缺失原因筛选候选，并打开详情逐条核对。
 * 状态筛选默认待核对；“全部状态”会显式传入四种状态，因为后端省略 status 时只返回待核对。
 * 核对状态与任务执行进度是两套状态；列表只展示候选摘要，逐字段取值、证据与操作由 ReviewDetail 负责。
 */
import { computed, onMounted, reactive, ref } from 'vue'

import { coursesApi, semestersApi } from '../api/business'
import { errorMessage } from '../api/client'
import { materialsApi } from '../api/library'
import { candidatesApi, type CandidateReviewFilters, type CandidateSummaryCounts } from '../api/review'
import type { CandidateKind, CandidateSummary, Course, Material, ReviewStatus, Semester } from '../api/types'
import ApiFeedback from '../components/ApiFeedback.vue'
import ReviewDetail from './ReviewDetail.vue'

const ALL_STATUSES: ReviewStatus[] = ['pending', 'confirmed', 'ignored', 'superseded']

const candidates = ref<CandidateSummary[]>([])
const summary = ref<CandidateSummaryCounts>({ total: 0, by_status: {}, by_reason: {} })
const courses = ref<Course[]>([])
const semesters = ref<Semester[]>([])
const materials = ref<Material[]>([])
const loading = ref(true)
const error = ref('')
const selectedId = ref<number | null>(null)
const filters = reactive({ status: 'pending', kind: '', courseId: '', semesterId: '', materialId: '', reason: '' })

const reviewStatusLabels: Record<ReviewStatus, string> = {
  pending: '待核对', confirmed: '已确认', ignored: '已忽略', superseded: '已失效',
}
const kindLabels: Record<CandidateKind, string> = { task: '任务', event: '日程', rule: '规则' }
const reasonLabels: Record<string, string> = {
  missing_title: '缺标题', missing_activity_text: '缺活动内容', missing_year: '缺年份',
  missing_time: '缺时刻', invalid_date: '日期无效', relative_date: '相对日期待换算',
  deadline_needs_review: '截止时间待核对', truncated: '摘录被截断',
}

const courseMap = computed(() => new Map(courses.value.map((course) => [course.id, course])))
const filteredCourses = computed(() => filters.semesterId
  ? courses.value.filter((course) => course.semester_id === Number(filters.semesterId))
  : courses.value)
const reasonCodes = computed(() => Object.keys(summary.value.by_reason))
const statusBreakdown = computed(() => ALL_STATUSES
  .filter((status) => summary.value.by_status[status])
  .map((status) => `${reviewStatusLabels[status]} ${summary.value.by_status[status]}`)
  .join(' · '))

function reasonLabel(code: string): string {
  return reasonLabels[code] ?? code
}

function currentFilters(): CandidateReviewFilters {
  return {
    // “全部”必须显式带上四种状态：后端省略 status 时只返回待核对。
    statuses: filters.status ? [filters.status as ReviewStatus] : ALL_STATUSES,
    kind: (filters.kind || null) as CandidateKind | null,
    courseId: filters.courseId ? Number(filters.courseId) : null,
    semesterId: filters.semesterId ? Number(filters.semesterId) : null,
    materialId: filters.materialId ? Number(filters.materialId) : null,
    reason: filters.reason || null,
  }
}

function scopeText(candidate: CandidateSummary): string {
  const course = candidate.course_id === null ? undefined : courseMap.value.get(candidate.course_id)
  return `${course?.name ?? '未关联课程'}${candidate.class_name ? ` · ${candidate.class_name}` : ''}`
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const current = currentFilters()
    // 汇总计数不随状态与原因筛选变化（下拉里的原因选项取自 by_reason），但跟随类型、课程、学期与来源资料。
    const summaryFilters = {
      kind: current.kind,
      courseId: current.courseId,
      semesterId: current.semesterId,
      materialId: current.materialId,
    }
    ;[candidates.value, summary.value, courses.value, semesters.value, materials.value] = await Promise.all([
      candidatesApi.list(current),
      candidatesApi.summary(summaryFilters),
      coursesApi.list(),
      semestersApi.list(true),
      materialsApi.list({ includeArchived: true }),
    ])
  } catch (caught) {
    error.value = errorMessage(caught)
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <main class="page-content">
    <div class="page-heading page-heading-row">
      <div>
        <p class="eyebrow">内容核对</p>
        <h1>待核对</h1>
        <p class="intro">集中核对从资料中提取的候选事项：逐条对照原文引用，确认后才成为正式任务与日程。</p>
        <p class="form-help">核对状态（待核对／已确认／已忽略／已失效）与任务执行进度相互独立；缺少必须字段或引用失效的事项不会被静默确认。</p>
      </div>
    </div>

    <ApiFeedback v-if="error" :message="error" :busy="loading" @retry="load" />
    <template v-else>
      <section class="panel filter-bar" aria-label="候选事项筛选">
        <label>核对状态<select v-model="filters.status" @change="load"><option value="">全部状态</option><option v-for="(label, value) in reviewStatusLabels" :key="value" :value="value">{{ label }}</option></select></label>
        <label>事项类型<select v-model="filters.kind" @change="load"><option value="">全部类型</option><option v-for="(label, value) in kindLabels" :key="value" :value="value">{{ label }}</option></select></label>
        <label>学期<select v-model="filters.semesterId" @change="filters.courseId = ''; load()"><option value="">全部学期</option><option v-for="semester in semesters" :key="semester.id" :value="semester.id">{{ semester.name }}{{ semester.archived ? '（已归档）' : '' }}</option></select></label>
        <label>课程<select v-model="filters.courseId" @change="load"><option value="">全部课程</option><option v-for="course in filteredCourses" :key="course.id" :value="course.id">{{ course.name }} · {{ course.class_name || '未分班' }}</option></select></label>
        <label>来源资料<select v-model="filters.materialId" @change="load"><option value="">全部资料</option><option v-for="material in materials" :key="material.id" :value="material.id">{{ material.title }}</option></select></label>
        <label>缺失原因<select v-model="filters.reason" @change="load"><option value="">全部原因</option><option v-for="code in reasonCodes" :key="code" :value="code">{{ reasonLabel(code) }}</option></select></label>
        <span class="count-badge">{{ candidates.length }} 项</span>
      </section>

      <section class="panel">
        <div class="section-heading">
          <div><p class="eyebrow">候选事项</p><h2>核对列表</h2></div>
          <button type="button" class="text-button" @click="load">刷新</button>
        </div>
        <p v-if="statusBreakdown" class="status-breakdown" role="status">{{ statusBreakdown }}</p>
        <p v-if="loading" class="muted">正在加载候选事项…</p>
        <div v-else-if="candidates.length" class="task-list">
          <article v-for="candidate in candidates" :key="candidate.id" class="task-row candidate-row">
            <div class="task-main">
              <div class="task-title-line">
                <h3>{{ candidate.title || '未提取到标题' }}</h3>
                <span class="status-pill kind-pill">{{ kindLabels[candidate.kind] }}</span>
                <span class="status-pill review-pill" :data-status="candidate.review_status">{{ reviewStatusLabels[candidate.review_status] }}</span>
              </div>
              <p>{{ scopeText(candidate) }} · 来源：资料 #{{ candidate.source_material_id }} · 版本 #{{ candidate.version_no }}</p>
              <div v-if="candidate.review_reasons.length" class="chip-list">
                <!-- 缺失原因由后端给出代码与说明，前端只做标签映射，不推断补齐。 -->
                <span v-for="reason in candidate.review_reasons" :key="`${reason.code}-${reason.field ?? ''}`" class="reason-chip">{{ reason.message }}</span>
              </div>
            </div>
            <div class="task-actions"><button type="button" class="button-secondary" @click="selectedId = candidate.id">核对</button></div>
          </article>
        </div>
        <div v-else class="compact-empty">{{ summary.total ? '没有符合当前筛选的候选事项。' : '还没有提取结果，先从资料库上传通知或课件并提取候选事项。' }}</div>
      </section>

      <!-- key 绑定候选 ID：切换候选时重建详情，避免沿用上一条的修正表单与提示。 -->
      <ReviewDetail
        v-if="selectedId !== null"
        :key="selectedId"
        :candidate-id="selectedId"
        @close="selectedId = null"
        @changed="load"
      />
    </template>
  </main>
</template>
