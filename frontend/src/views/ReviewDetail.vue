<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'

import { errorMessage } from '../api/client'
import { candidatesApi } from '../api/review'
import type { CandidateEvidence, CandidateFieldView, CandidateKind, CandidateReviewDetail, FieldOrigin, ReviewStatus } from '../api/types'
import ApiFeedback from '../components/ApiFeedback.vue'

const props = defineProps<{ candidateId: number }>()
const emit = defineEmits<{ close: []; changed: [] }>()

const detail = ref<CandidateReviewDetail | null>(null)
const loading = ref(true)
const loadError = ref('')
const saving = ref(false)
const actionError = ref('')
const successMessage = ref('')
const reasonText = ref('')
const acknowledge = ref(false)

const reviewStatusLabels: Record<ReviewStatus, string> = {
  pending: '待核对', confirmed: '已确认', ignored: '已忽略', superseded: '已失效',
}
const kindLabels: Record<CandidateKind, string> = { task: '任务', event: '日程', rule: '规则' }
const originLabels: Record<FieldOrigin, string> = {
  document: '原文规定', user: '用户补充', inferred: '系统推断', suggestion: '规划建议',
}
const transitionLabels: Record<ReviewStatus, string> = {
  pending: '退回待核对', confirmed: '确认', ignored: '忽略', superseded: '标记失效',
}
const fieldLabels: Record<string, string> = {
  title: '标题', activity_text: '活动内容', due_date: '截止日期', due_time: '截止时刻',
  deadline: '截止时间', starts_at: '开始时间', ends_at: '结束时间', location: '地点',
  duration_minutes: '预计耗时', estimated_minutes: '预计耗时建议', priority: '优先级',
  rule_text: '规则正文', conditions_text: '参加条件', scope_text: '适用范围', effective_text: '生效说明',
}

function fieldLabel(name: string): string {
  return fieldLabels[name] ?? name
}

function locatorLabel(evidence: CandidateEvidence): string {
  if (!evidence.locator_type) return '未返回位置信息'
  if (evidence.locator_type === 'paragraph') return `第 ${evidence.locator_value} 段`
  if (evidence.locator_type === 'page') return `第 ${evidence.locator_value} 页`
  return evidence.locator_value
}

/** 定位链接固定到引用所属版本，避免资料有新版本时高亮不到这条摘录。 */
function citationLink(evidence: CandidateEvidence) {
  const source = detail.value?.source
  return {
    path: '/library',
    query: {
      material: String(source?.material_id ?? ''),
      version: String(source?.version_no ?? ''),
      chunk: String(evidence.chunk_id),
    },
  }
}

function scopeText(scope: CandidateReviewDetail['scope']): string {
  const parts = [
    // 后端只补本空间的名称；拿不到名称时退回 ID，不拿 ID 冒充名称
    scope.course_name ?? (scope.course_id === null ? '未关联课程' : `课程 #${scope.course_id}`),
    scope.semester_name ?? (scope.semester_id === null ? '未关联学期' : `学期 #${scope.semester_id}`),
    scope.class_name ? `教学班 ${scope.class_name}` : '未关联教学班',
  ]
  return parts.join(' · ')
}

function displayValue(value: unknown): string {
  if (value === null || value === undefined) return '（空）'
  if (typeof value === 'string') return value || '（空）'
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  try {
    return JSON.stringify(value)
  } catch {
    return String(value)
  }
}

function changed(field: CandidateFieldView): boolean {
  return JSON.stringify(field.value) !== JSON.stringify(field.effective_value)
}

function transitionClass(to: ReviewStatus): string {
  if (to === 'superseded') return 'danger-button'
  return to === 'confirmed' ? '' : 'button-secondary'
}

function formatDateTime(value: string): string {
  return new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value))
}

async function loadDetail(): Promise<void> {
  loading.value = true
  loadError.value = ''
  try {
    detail.value = await candidatesApi.get(props.candidateId)
  } catch (caught) {
    loadError.value = errorMessage(caught)
  } finally {
    loading.value = false
  }
}

async function transition(to: ReviewStatus) {
  saving.value = true
  actionError.value = ''
  successMessage.value = ''
  try {
    // 后端只接受其允许的转换；缺必须字段时必须同时带上报错的核对说明与显式确认。
    detail.value = await candidatesApi.setReviewStatus(props.candidateId, {
      status: to,
      reason: reasonText.value.trim() || null,
      acknowledge_missing: acknowledge.value,
    })
    successMessage.value = `核对状态已更新为「${reviewStatusLabels[to]}」。`
    emit('changed')
  } catch (caught) {
    // 失败时错误显示在按钮上方，成功提示被清空且不会掩盖错误；
    // 不重新加载详情，保留用户已填写的核对说明，修正后可直接重试。
    actionError.value = errorMessage(caught)
    successMessage.value = ''
  } finally {
    saving.value = false
  }
}

onMounted(loadDetail)
watch(() => props.candidateId, loadDetail)
</script>

<template>
  <section class="panel editor-panel review-detail-panel">
    <div class="section-heading">
      <div><p class="eyebrow">候选核对</p><h2>候选 #{{ candidateId }}</h2></div>
      <button type="button" class="text-button" @click="emit('close')">关闭</button>
    </div>

    <ApiFeedback v-if="loadError" :message="loadError" :busy="loading" @retry="loadDetail" />
    <p v-else-if="loading" class="muted">正在加载候选详情…</p>
    <template v-else-if="detail">
      <div class="review-status-line">
        <span class="status-pill kind-pill">{{ kindLabels[detail.kind] }}</span>
        <span class="status-pill review-pill" :data-status="detail.review_status">{{ reviewStatusLabels[detail.review_status] }}</span>
        <span class="muted small">提取于 {{ formatDateTime(detail.created_at) }}</span>
      </div>

      <h3 class="subheading">来源</h3>
      <div class="source-brief">
        <p><strong>{{ detail.source.material_title }}</strong></p>
        <p>资料 #{{ detail.source.material_id }} · 版本 #{{ detail.source.version_no }}</p>
        <p>适用范围：{{ scopeText(detail.scope) }}</p>
        <router-link class="text-button" :to="{ path: '/library', query: { material: String(detail.source.material_id), version: String(detail.source.version_no) } }">到资料库查看原文定位</router-link>
      </div>

      <div v-if="detail.missing_required_fields.length || detail.review_reasons.length" class="coverage-box">
        <strong>待补充与待核对项</strong>
        <p v-if="detail.missing_required_fields.length" role="status">必须字段仍为空：{{ detail.missing_required_fields.map(fieldLabel).join('、') }}。确认前需先补充字段，或填写核对说明并显式确认。</p>
        <p v-for="reason in detail.review_reasons" :key="`${reason.code}-${reason.field ?? ''}`">{{ reason.field ? `${fieldLabel(reason.field)}：` : '' }}{{ reason.message }}</p>
      </div>

      <h3 class="subheading">字段核对</h3>
      <div v-if="detail.fields.length" class="field-review-list">
        <article v-for="field in detail.fields" :key="field.name" class="field-review">
          <div class="field-review-head">
            <strong>{{ fieldLabel(field.name) }}</strong>
            <span class="status-pill origin-pill" :data-origin="field.effective_origin">{{ originLabels[field.effective_origin] }}</span>
            <span v-if="!field.verifiable" class="status-pill stale-pill">字段值引用已失效</span>
          </div>
          <p>生效值：{{ displayValue(field.effective_value) }}</p>
          <p v-if="changed(field)">原始提取：{{ displayValue(field.value) }}（出处：{{ originLabels[field.origin] }}）</p>
          <p v-if="field.explanation">{{ field.explanation }}</p>
          <p v-if="field.missing_reason" class="inline-notice">{{ field.missing_reason }}</p>
          <div v-if="field.evidence.length" class="evidence-list">
            <figure v-for="evidence in field.evidence" :key="evidence.chunk_id" class="evidence-quote" :class="{ 'is-stale': !evidence.verifiable }">
              <blockquote>{{ evidence.quote }}</blockquote>
              <figcaption class="evidence-caption">
                <span>片段 #{{ evidence.chunk_id }} · {{ locatorLabel(evidence) }}</span>
                <router-link class="text-button" :to="citationLink(evidence)">定位原文</router-link>
                <span class="status-pill" :class="evidence.verifiable ? 'verified-pill' : 'stale-pill'">{{ evidence.verifiable ? '引用可验证' : '引用已失效，无法在原文中找到' }}</span>
              </figcaption>
            </figure>
          </div>
          <p v-else class="form-help">该字段没有可定位的原文引用。</p>
        </article>
      </div>
      <div v-else class="compact-empty">该候选还没有提取到任何字段。</div>

      <h3 class="subheading">修正留痕</h3>
      <ol v-if="detail.revisions.length" class="history-list">
        <li v-for="revision in detail.revisions" :key="revision.id">
          <div><strong>{{ fieldLabel(revision.field_name) }}：{{ displayValue(revision.previous_value) }} → {{ displayValue(revision.new_value) }}</strong><time>{{ formatDateTime(revision.created_at) }}</time></div>
          <p>出处：{{ originLabels[revision.previous_origin] }} → {{ originLabels[revision.new_origin] }} · 操作者：{{ revision.operator }}</p>
          <small>{{ revision.note || '未填写说明' }}</small>
        </li>
      </ol>
      <div v-else class="compact-empty">还没有字段修正记录；原始提取与证据不会被改写。</div>

      <h3 class="subheading">核对留痕</h3>
      <ol v-if="detail.review_changes.length" class="history-list">
        <li v-for="change in detail.review_changes" :key="change.id">
          <div><strong>{{ reviewStatusLabels[change.from_status] }} → {{ reviewStatusLabels[change.to_status] }}</strong><time>{{ formatDateTime(change.changed_at) }}</time></div>
          <p>{{ change.reason || '未填写原因' }} · 操作者：{{ change.operator }}</p>
        </li>
      </ol>
      <div v-else class="compact-empty">还没有核对状态变更记录。</div>

      <h3 class="subheading">核对操作</h3>
      <div class="stack-form">
        <label>核对说明（缺少必须字段时必填）<textarea v-model.trim="reasonText" rows="3" maxlength="500" placeholder="例如：已对照第 2 页原文确认 DDL；缺少年份时保留待核对，不补当前年"></textarea></label>
        <label v-if="detail.missing_required_fields.length" class="switch-field"><input v-model="acknowledge" type="checkbox" />我已确认缺少必须字段，仍要继续</label>
        <p v-if="actionError" class="form-error" role="alert">{{ actionError }}</p>
        <p v-if="successMessage" class="success-message" role="status">{{ successMessage }}</p>
        <div class="lifecycle-actions">
          <button
            v-for="next in detail.allowed_transitions"
            :key="next"
            type="button"
            :class="transitionClass(next)"
            :disabled="saving"
            @click="transition(next)"
          >{{ saving ? '正在提交…' : transitionLabels[next] }}</button>
          <span v-if="!detail.allowed_transitions.length" class="muted small">当前状态没有可用的核对转换。</span>
        </div>
        <p class="form-help">转换范围由后端按核对规则给出；已失效事项只能退回待核对。确认后的事项才会进入正式任务与日程。</p>
      </div>
    </template>
  </section>
</template>
