<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { errorMessage } from '../api/client'
import { candidatesApi } from '../api/review'
import type { CandidateDiff, CandidateSummary, ChangeImpact, VersionAcceptance, VersionApplicability } from '../api/types'
import ApiFeedback from '../components/ApiFeedback.vue'

const route = useRoute()
const router = useRouter()

const candidates = ref<CandidateSummary[]>([])
const baselineId = ref<number | null>(null)
const comparisonId = ref<number | null>(null)
const diff = ref<CandidateDiff | null>(null)
const applicability = ref<VersionApplicability | null>(null)
const impact = ref<ChangeImpact | null>(null)
const history = ref<VersionAcceptance[]>([])
const loading = ref(true)
const error = ref('')
const acceptError = ref('')
const accepting = ref(false)
const selectedFields = ref<string[]>([])
const reason = ref('')
const acknowledgeConflict = ref(false)
const accepted = ref<VersionAcceptance | null>(null)

const statusLabels: Record<string, string> = {
  unchanged: '无变化', changed: '已修改', added: '新增', missing: '缺失', explicitly_removed: '原文明确删除',
}
const applicabilityLabels: Record<string, string> = {
  applicable: '可作为新版本', conflict: '冲突，需核对', requires_review: '证据不足，待核对',
}
const comparable = computed(() => candidates.value.filter((item) => item.id !== baselineId.value))
const changedFields = computed(() => diff.value?.fields.filter((field) => field.status !== 'unchanged') ?? [])
const scopeText = (scope: CandidateDiff['left']['scope']) => [
  scope.semester_name ?? '未指定学期',
  scope.course_name ?? '未关联课程',
  scope.class_name ?? '未指定教学班',
].join(' · ')
const valueText = (value: unknown) => value === null || value === undefined ? '—' : String(value)

function queryNumber(value: unknown): number | null {
  const parsed = Number(value)
  return Number.isInteger(parsed) && parsed > 0 ? parsed : null
}

async function loadCandidates() {
  loading.value = true
  error.value = ''
  try {
    candidates.value = await candidatesApi.list({ statuses: ['pending', 'confirmed', 'ignored', 'superseded'] })
    baselineId.value = queryNumber(route.query.baseline) ?? candidates.value[0]?.id ?? null
  } catch (caught) {
    error.value = errorMessage(caught)
  } finally {
    loading.value = false
  }
}

async function loadComparison() {
  if (baselineId.value === null || comparisonId.value === null) {
    error.value = '请选择基准候选和待比较候选。'
    return
  }
  error.value = ''
  acceptError.value = ''
  accepted.value = null
  try {
    const [nextDiff, nextApplicability, nextImpact, nextHistory] = await Promise.all([
      candidatesApi.diff(baselineId.value, comparisonId.value),
      candidatesApi.applicability(baselineId.value, comparisonId.value),
      candidatesApi.impact(baselineId.value, comparisonId.value),
      candidatesApi.acceptances(baselineId.value),
    ])
    diff.value = nextDiff
    applicability.value = nextApplicability
    impact.value = nextImpact
    history.value = nextHistory
    selectedFields.value = nextDiff.fields
      .filter((field) => field.status !== 'unchanged')
      .map((field) => field.name)
    await router.replace({ query: { baseline: String(baselineId.value), comparison: String(comparisonId.value) } })
  } catch (caught) {
    error.value = errorMessage(caught)
  }
}

async function submitAcceptance() {
  if (baselineId.value === null || comparisonId.value === null) return
  if (!selectedFields.value.length) {
    acceptError.value = '请至少选择一个要接受的字段差异。'
    return
  }
  if (!reason.value.trim()) {
    acceptError.value = '请填写接受原因。'
    return
  }
  accepting.value = true
  acceptError.value = ''
  try {
    const result = await candidatesApi.accept(baselineId.value, comparisonId.value, {
      field_names: selectedFields.value,
      reason: reason.value.trim(),
      acknowledge_conflict: acknowledgeConflict.value,
    })
    await loadComparison()
    accepted.value = result
  } catch (caught) {
    acceptError.value = errorMessage(caught)
  } finally {
    accepting.value = false
  }
}

function toggleField(name: string) {
  selectedFields.value = selectedFields.value.includes(name)
    ? selectedFields.value.filter((item) => item !== name)
    : [...selectedFields.value, name]
}

onMounted(loadCandidates)
</script>

<template>
  <main class="page-content">
    <div class="page-heading">
      <p class="eyebrow">通知变更</p>
      <h1>版本对照</h1>
      <p class="intro">并列查看新旧版本的原文、字段差异、适用范围和受影响事项。接受前不会修改当前安排。</p>
    </div>

    <ApiFeedback v-if="error" :message="error" :busy="loading" @retry="loadCandidates" />
    <template v-else>
      <section class="panel filter-bar" aria-label="选择比较版本">
        <label>基准（旧）<select v-model="baselineId"><option v-for="item in candidates" :key="item.id" :value="item.id">#{{ item.id }} · {{ item.title ?? '未命名' }}</option></select></label>
        <label>待比较（新）<select v-model="comparisonId"><option value="">请选择</option><option v-for="item in comparable" :key="item.id" :value="item.id">#{{ item.id }} · {{ item.title ?? '未命名' }}</option></select></label>
        <button type="button" @click="loadComparison">加载对照</button>
      </section>

      <template v-if="diff && applicability">
        <section class="panel">
          <div class="section-heading"><div><p class="eyebrow">适用范围</p><h2>版本归属</h2></div><span class="status-pill" :data-status="applicability.status">{{ applicabilityLabels[applicability.status] }}</span></div>
          <div class="material-list">
            <article class="material-row">
              <div class="material-main">
                <h3>旧版本 · {{ diff.left.material_title }} · 版本 {{ diff.left.version_no }}</h3>
                <p>{{ scopeText(diff.left.scope) }}</p>
                <p class="form-help">发布时间：{{ applicability.publication.baseline?.published_at ?? applicability.publication.baseline?.published_text ?? '未提供完整发布时间' }}</p>
              </div>
              <div class="material-main">
                <h3>新版本 · {{ diff.right.material_title }} · 版本 {{ diff.right.version_no }}</h3>
                <p>{{ scopeText(diff.right.scope) }}</p>
                <p class="form-help">发布时间：{{ applicability.publication.comparison?.published_at ?? applicability.publication.comparison?.published_text ?? '未提供完整发布时间' }}</p>
              </div>
            </article>
          </div>
          <div class="coverage-box" :class="{ failed: applicability.status === 'conflict' }">
            <strong>{{ applicabilityLabels[applicability.status] }}</strong>
            <ul><li v-for="item in applicability.reasons" :key="item.code">{{ item.message }}</li></ul>
          </div>
        </section>

        <section class="panel">
          <div class="section-heading"><div><p class="eyebrow">字段差异</p><h2>选择要接受的字段</h2></div></div>
          <div class="material-list">
            <article v-for="field in changedFields" :key="field.name" class="material-row">
              <div class="material-main">
                <div class="task-title-line">
                  <label><input type="checkbox" :checked="selectedFields.includes(field.name)" @change="toggleField(field.name)" /> {{ field.name }}</label>
                  <span class="status-pill">{{ statusLabels[field.status] }}</span>
                </div>
                <p>旧：{{ valueText(field.left.effective_value) }}</p>
                <p>新：{{ valueText(field.right.effective_value) }}</p>
                <p v-if="field.left.evidence[0]" class="form-help">旧原文：{{ field.left.evidence[0].quote }}（{{ field.left.evidence[0].locator_value }}，{{ field.left.evidence[0].verifiable ? '可验证' : '不可验证' }}）</p>
                <p v-if="field.right.evidence[0]" class="form-help">新原文：{{ field.right.evidence[0].quote }}（{{ field.right.evidence[0].locator_value }}，{{ field.right.evidence[0].verifiable ? '可验证' : '不可验证' }}）</p>
              </div>
            </article>
          </div>
          <p v-if="!changedFields.length" class="compact-empty">两个候选没有字段差异。</p>
        </section>

        <section v-if="impact" class="panel">
          <div class="section-heading"><div><p class="eyebrow">受影响事项</p><h2>任务与时间块</h2></div></div>
          <div v-if="impact.tasks.length" class="material-list">
            <article v-for="task in impact.tasks" :key="task.id" class="material-row">
              <div class="material-main">
                <h3>{{ task.title }}</h3>
                <p>进度：{{ task.progress }}{{ task.completed ? '（已完成）' : '' }} · {{ task.due_date ?? '无日期' }}</p>
                <p v-for="block in task.plan_blocks" :key="block.id" class="form-help">时间块 {{ block.starts_at }} – {{ block.ends_at }}{{ block.locked ? ' · 已锁定' : '' }} · 草案 {{ block.draft_status }}</p>
              </div>
            </article>
          </div>
          <p v-else class="compact-empty">旧版本尚未转换为正式任务。</p>
          <p v-for="item in impact.unsupported" :key="item.code" class="inline-notice">{{ item.message }}</p>
        </section>

        <section class="panel">
          <div class="section-heading"><div><p class="eyebrow">确认</p><h2>接受所选字段</h2></div></div>
          <p class="form-help">接受后旧候选会标记为已失效；未点击接受前，任务、日程和计划不会改变。</p>
          <label class="switch-field"><input v-model="acknowledgeConflict" type="checkbox" />我已核对冲突并确认接受</label>
          <label>接受原因<textarea v-model="reason" rows="3" placeholder="说明为什么接受这些字段"></textarea></label>
          <p v-if="acceptError" class="form-error" role="alert">{{ acceptError }}</p>
          <p v-if="accepted" class="success-message" role="status">已接受：{{ accepted.accepted_fields.join('、') }}。旧候选状态：{{ accepted.baseline_review_status }}</p>
          <button type="button" :disabled="accepting" @click="submitAcceptance">{{ accepting ? '正在接受…' : '接受所选差异' }}</button>
        </section>

        <section class="panel">
          <div class="section-heading"><div><p class="eyebrow">历史</p><h2>变更接受记录</h2></div></div>
          <div v-if="history.length" class="material-list">
            <article v-for="record in history" :key="record.id" class="material-row">
              <div class="material-main">
                <h3>#{{ record.id }} · {{ record.accepted_fields.join('、') }}</h3>
                <p>旧版本 {{ record.baseline_version_id }} → 新版本 {{ record.comparison_version_id }} · {{ record.created_at }}</p>
                <p>原因：{{ record.reason }} · 操作者：{{ record.operator }}</p>
                <p v-for="item in record.evidence_snapshot" :key="item.name" class="form-help">{{ item.name }}：{{ valueText(item.left.effective_value) }} → {{ valueText(item.right.effective_value) }}</p>
              </div>
            </article>
          </div>
          <p v-else class="compact-empty">还没有接受记录。</p>
        </section>
      </template>
    </template>
  </main>
</template>
