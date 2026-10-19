<script setup lang="ts">
/*
 * 版本对照页：选择基准（旧）与新候选后，并列展示适用范围、字段差异、受影响事项与重复建议。
 * 流程：加载对照时并行取差异、适用性、影响与历史；用户勾选字段并填写原因后才接受；重复合并须先预览拿令牌。
 * 约束：接受前不改变任务、日程和计划；冲突或证据不足需显式确认；取消合并预览不影响原候选。
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { errorMessage } from '../api/client'
import { candidatesApi } from '../api/review'
import type { CandidateDiff, CandidateSummary, ChangeImpact, DuplicateReport, MergePreview, MergeResult, VersionAcceptance, VersionApplicability } from '../api/types'
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
const duplicateReport = ref<DuplicateReport | null>(null)
const selectedSources = ref<number[]>([])
const mergePreview = ref<MergePreview | null>(null)
const mergeReason = ref('')
const mergeError = ref('')
const merging = ref(false)
const mergeResult = ref<MergeResult | null>(null)

// missing 只表示新侧没有该字段；explicitly_removed 需新侧字段存在、值为空且至少一条证据仍可逐字定位。
const statusLabels: Record<string, string> = {
  unchanged: '无变化', changed: '已修改', added: '新增', missing: '缺失', explicitly_removed: '原文明确删除',
}
const applicabilityLabels: Record<string, string> = {
  applicable: '可作为新版本', conflict: '冲突，需核对', requires_review: '证据不足，待核对',
}
const comparable = computed(() => candidates.value.filter((item) => item.id !== baselineId.value))
// 只有实际变化的字段可被接受；无变化字段后端会拒绝，因此这里不展示。
const changedFields = computed(() => diff.value?.fields.filter((field) => field.status !== 'unchanged') ?? [])
const scopeText = (scope: CandidateDiff['left']['scope']) => [
  scope.semester_name ?? '未指定学期',
  scope.course_name ?? '未关联课程',
  scope.class_name ?? '未指定教学班',
].join(' · ')
const valueText = (value: unknown) => value === null || value === undefined ? '—' : String(value)

// 地址栏参数可能是数组或非法字符串，只接受正整数候选 ID。
function queryNumber(value: unknown): number | null {
  const parsed = Number(value)
  return Number.isInteger(parsed) && parsed > 0 ? parsed : null
}

// 一并列出已失效候选以保留旧版本和接受历史；基准优先取地址栏参数，其次取列表首项。
async function loadCandidates() {
  loading.value = true
  error.value = ''
  try {
    candidates.value = await candidatesApi.list({ statuses: ['pending', 'confirmed', 'ignored', 'superseded'] })
    baselineId.value = queryNumber(route.query.baseline) ?? candidates.value[0]?.id ?? null
    await loadDuplicates()
  } catch (caught) {
    error.value = errorMessage(caught)
  } finally {
    loading.value = false
  }
}

// 重复建议只读；切换基准后清空来源选择与旧预览，避免预览令牌跨范围复用。
async function loadDuplicates() {
  if (baselineId.value === null) return
  try {
    duplicateReport.value = await candidatesApi.duplicates(baselineId.value)
    selectedSources.value = []
    mergePreview.value = null
  } catch (caught) {
    mergeError.value = errorMessage(caught)
  }
}

// 来源选择变化会使预览令牌失效，因此同时丢弃预览和已合并结果。
function toggleSource(candidateId: number) {
  selectedSources.value = selectedSources.value.includes(candidateId)
    ? selectedSources.value.filter((item) => item !== candidateId)
    : [...selectedSources.value, candidateId]
  mergePreview.value = null
  mergeResult.value = null
}

// 预览令牌绑定目标、来源候选、版本、进度与时间块数量；合并时原样回传，由后端重新校验。
async function loadMergePreview() {
  if (baselineId.value === null || !selectedSources.value.length) {
    mergeError.value = '请先选择要合并的重复候选。'
    return
  }
  mergeError.value = ''
  mergeResult.value = null
  try {
    mergePreview.value = await candidatesApi.mergePreview(baselineId.value, selectedSources.value)
  } catch (caught) {
    mergeError.value = errorMessage(caught)
  }
}

function cancelMerge() {
  // 取消只清空预览，不隐藏原始候选或重复列表。
  mergePreview.value = null
  mergeError.value = ''
}

// 合并原因必填；若预览后范围或进度变化，后端返回冲突要求重新预览，不会按旧令牌执行。
async function submitMerge() {
  if (baselineId.value === null || !mergePreview.value) return
  if (!mergeReason.value.trim()) {
    mergeError.value = '请填写合并原因。'
    return
  }
  merging.value = true
  mergeError.value = ''
  try {
    mergeResult.value = await candidatesApi.merge(baselineId.value, {
      source_candidate_ids: selectedSources.value,
      preview_token: mergePreview.value.token,
      reason: mergeReason.value.trim(),
    })
    mergePreview.value = null
    await loadComparison()
    await loadDuplicates()
  } catch (caught) {
    mergeError.value = errorMessage(caught)
  } finally {
    merging.value = false
  }
}

// 四个只读请求并行获取；默认勾选全部变化字段，但仍需用户填写原因并显式接受。
// 两侧候选写入地址栏，刷新或分享后仍能恢复同一组对照。
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

// 字段与原因都要显式提交；判定为冲突或待核对时 acknowledge_conflict 为必需。
// 接受成功后旧候选标记失效并重算提醒，页面随即刷新对照与历史。
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
          <!-- 后端声明的未覆盖能力必须原样展示，不能省略或让用户以为已同步。 -->
          <p v-for="item in impact.unsupported" :key="item.code" class="inline-notice">{{ item.message }}</p>
        </section>

        <section class="panel">
          <div class="section-heading"><div><p class="eyebrow">重复建议</p><h2>合并预览</h2></div></div>
          <p class="form-help">重复建议只供核对；必须预览并确认后才会合并。取消或请求失败不会隐藏原候选。</p>
          <div v-if="duplicateReport?.suggestions.length" class="material-list">
            <article v-for="item in duplicateReport.suggestions" :key="item.candidate_id" class="material-row">
              <div class="material-main">
                <div class="task-title-line">
                  <label><input type="checkbox" :checked="selectedSources.includes(item.candidate_id)" @change="toggleSource(item.candidate_id)" /> #{{ item.candidate_id }} · {{ item.title ?? '未命名' }}</label>
                  <span class="status-pill">相似度 {{ item.score }}</span>
                </div>
                <p>{{ item.reasons.join(' · ') }} · 来源版本 {{ item.source_version_id }}</p>
                <p v-if="!item.scope_same" class="form-help">范围信息不完整，请人工核对。</p>
              </div>
            </article>
          </div>
          <p v-else class="compact-empty">没有重复建议。</p>
          <p v-for="item in duplicateReport?.excluded ?? []" :key="item.candidate_id" class="form-help">已排除 #{{ item.candidate_id }}：{{ item.message }}</p>
          <button type="button" @click="loadMergePreview">预览合并</button>

          <div v-if="mergePreview" class="coverage-box">
            <strong>将保留目标 #{{ mergePreview.target_candidate_id }} 的字段</strong>
            <p>{{ Object.entries(mergePreview.kept_fields).map(([key, value]) => `${key}=${valueText(value)}`).join(' · ') }}</p>
            <ul>
              <li v-for="source in mergePreview.sources" :key="source.candidate_id">
                合并 #{{ source.candidate_id }}（{{ source.title ?? '未命名' }}）：来源进度 {{ source.progress ?? '无' }}，时间块 {{ source.plan_block_count }} 个
              </li>
            </ul>
            <label>合并原因<textarea v-model="mergeReason" rows="2" placeholder="说明为什么这些事项是同一项"></textarea></label>
            <p v-if="mergeError" class="form-error" role="alert">{{ mergeError }}</p>
            <div class="material-actions">
              <button type="button" class="button-secondary" @click="cancelMerge">取消预览</button>
              <button type="button" :disabled="merging" @click="submitMerge">{{ merging ? '正在合并…' : '确认合并预览范围' }}</button>
            </div>
          </div>
          <p v-if="mergeError && !mergePreview" class="form-error" role="alert">{{ mergeError }}</p>
          <p v-if="mergeResult" class="success-message" role="status">已合并 {{ mergeResult.records.length }} 项；原候选与来源历史已保留。</p>
        </section>

        <section class="panel">
          <div class="section-heading"><div><p class="eyebrow">确认</p><h2>接受所选字段</h2></div></div>
          <p class="form-help">接受后旧候选会标记为已失效；未点击接受前，任务、日程和计划不会改变。</p>
          <!-- 适用性为冲突或证据不足时，必须勾选确认才能接受；后端会再次校验。 -->
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
