<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import { errorMessage } from '../api/client'
import { plansApi } from '../api/plans'
import type { PlanBlock, PlanConflict, PlanDraft } from '../api/plans'
import ApiFeedback from '../components/ApiFeedback.vue'

const draft = ref<PlanDraft | null>(null)
const current = ref<PlanDraft | null>(null)
const loading = ref(true)
const generating = ref(false)
const accepting = ref(false)
const busyBlockId = ref<number | null>(null)
const error = ref('')
const actionError = ref('')
const assumptions = reactive({
  default_duration_minutes: '',
  segment_minutes: '',
  buffer_minutes: '',
})
const moveForms = reactive<Record<number, { starts_at: string; ends_at: string }>>({})

const constraintLabels: Record<string, string> = {
  availability: '可用时间',
  priority: '优先级',
  daily_capacity: '每日容量',
  weekly_capacity: '每周容量',
  break: '休息间隔',
  segment: '分段',
  buffer: '提交缓冲',
  deadline: '截止',
  estimated_duration: '耗时估计',
}

const statusLabels: Record<string, string> = { draft: '待接受草案', accepted: '当前计划', superseded: '已被替代' }
const dispositionLabels: Record<string, string> = {
  scheduled: '已完整排入',
  partial: '部分排入',
  unscheduled: '未排入',
  excluded: '已排除',
}

const blocksByDay = computed(() => {
  const groups = new Map<string, PlanBlock[]>()
  for (const block of draft.value?.blocks ?? []) {
    const key = new Date(block.starts_at).toLocaleDateString('zh-CN', {
      month: 'long',
      day: 'numeric',
      weekday: 'short',
    })
    const list = groups.get(key) ?? []
    list.push(block)
    groups.set(key, list)
  }
  return [...groups.entries()]
})

const blockingConflicts = computed(() =>
  (draft.value?.conflicts ?? []).filter((item) => item.severity === 'conflict'),
)
const risks = computed(() => (draft.value?.conflicts ?? []).filter((item) => item.severity === 'risk'))
const adjustOptions = computed(() => draft.value?.coverage.adjustment_options ?? [])

function timeText(value: string): string {
  return new Date(value).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
}

function dateTimeText(value: string): string {
  return new Date(value).toLocaleString('zh-CN', { dateStyle: 'short', timeStyle: 'short' })
}

function toLocalInput(value: string): string {
  const date = new Date(value)
  const pad = (part: number) => String(part).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`
}

function prepareMove(block: PlanBlock) {
  moveForms[block.id] = {
    starts_at: toLocalInput(block.starts_at),
    ends_at: toLocalInput(block.ends_at),
  }
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    current.value = await plansApi.current()
    draft.value = current.value
  } catch (caught) {
    error.value = errorMessage(caught)
  } finally {
    loading.value = false
  }
}

async function generate() {
  generating.value = true
  error.value = ''
  actionError.value = ''
  try {
    draft.value = await plansApi.generate({
      assumptions: {
        default_duration_minutes: assumptions.default_duration_minutes
          ? Number(assumptions.default_duration_minutes)
          : null,
        segment_minutes: assumptions.segment_minutes ? Number(assumptions.segment_minutes) : null,
        buffer_minutes: assumptions.buffer_minutes ? Number(assumptions.buffer_minutes) : null,
      },
    })
  } catch (caught) {
    error.value = errorMessage(caught)
  } finally {
    generating.value = false
  }
}

async function accept() {
  if (!draft.value) return
  accepting.value = true
  actionError.value = ''
  try {
    const accepted = await plansApi.accept(draft.value.id, draft.value.version)
    draft.value = accepted
    current.value = accepted
  } catch (caught) {
    actionError.value = errorMessage(caught)
  } finally {
    accepting.value = false
  }
}

async function toggleLock(block: PlanBlock) {
  if (!draft.value) return
  busyBlockId.value = block.id
  actionError.value = ''
  try {
    draft.value = await plansApi.setLock(block.id, !block.locked, draft.value.version)
    if (draft.value.status === 'accepted') current.value = draft.value
  } catch (caught) {
    actionError.value = errorMessage(caught)
  } finally {
    busyBlockId.value = null
  }
}

async function saveMove(block: PlanBlock) {
  const form = moveForms[block.id]
  if (!draft.value || !form) return
  busyBlockId.value = block.id
  actionError.value = ''
  try {
    draft.value = await plansApi.moveBlock(
      block.id,
      new Date(form.starts_at).toISOString(),
      new Date(form.ends_at).toISOString(),
      draft.value.version,
    )
    if (draft.value.status === 'accepted') current.value = draft.value
    delete moveForms[block.id]
  } catch (caught) {
    actionError.value = errorMessage(caught)
  } finally {
    busyBlockId.value = null
  }
}

function conflictTitle(conflict: PlanConflict): string {
  const labels: Record<string, string> = {
    time_overlap: '时间重叠',
    deadline_risk: '截止风险',
    load_risk: '学习负荷风险',
  }
  return labels[conflict.type] ?? conflict.type
}

function showCurrentPlan() {
  draft.value = current.value
}

onMounted(load)
</script>

<template>
  <main class="page-content">
    <div class="page-heading page-heading-row">
      <div>
        <p class="eyebrow">任务与计划</p>
        <h1>两周学习计划</h1>
        <p class="intro">先按固定日程、明确截止和可用时间生成草案；确认后才成为当前计划。</p>
        <p class="form-help">调整时间块不会改写任务原始 DDL。未接受前不会影响现有安排。</p>
      </div>
      <div class="page-actions">
        <button type="button" :disabled="generating" @click="generate">
          {{ generating ? '生成中…' : '生成未来两周草案' }}
        </button>
        <button v-if="current && draft?.id !== current.id" type="button" class="button-secondary" @click="showCurrentPlan">
          查看当前计划
        </button>
      </div>
    </div>

    <ApiFeedback v-if="error" :message="error" />
    <ApiFeedback v-if="actionError" :message="actionError" />

    <section class="panel stack-form" aria-label="规划假设">
      <div>
        <p class="eyebrow">可选假设</p>
        <p class="form-help">留空表示不假设缺失信息；选择后的假设会随草案明确展示。</p>
      </div>
      <div class="form-grid form-grid-3">
        <label>缺失耗时按（分钟）<input v-model="assumptions.default_duration_minutes" type="number" min="1" placeholder="不假设" /></label>
        <label>单段最长（分钟）<input v-model="assumptions.segment_minutes" type="number" min="1" placeholder="90" /></label>
        <label>提交缓冲（分钟）<input v-model="assumptions.buffer_minutes" type="number" min="0" placeholder="按设置" /></label>
      </div>
    </section>

    <section v-if="loading" class="panel compact-empty">正在读取计划…</section>
    <section v-else-if="!draft" class="panel compact-empty">
      尚未生成计划草案。设置可用时间并添加带耗时的任务后，点击“生成未来两周草案”。
    </section>

    <template v-else>
      <section class="panel plan-summary" aria-label="草案状态">
        <div class="meta-row">
          <span>状态</span><strong>{{ statusLabels[draft.status] }}</strong>
          <span>版本</span><strong>v{{ draft.version }}</strong>
          <span>范围</span><strong>{{ draft.range_start }} 至 {{ draft.range_end }}</strong>
          <span>容量缺口</span><strong>{{ draft.coverage.capacity_gap_minutes }} 分钟</strong>
        </div>
        <p class="form-help">
          任务 {{ draft.coverage.tasks_considered }} 项：完整排入 {{ draft.coverage.tasks_scheduled }}，
          部分排入 {{ draft.coverage.tasks_partial }}，未排入 {{ draft.coverage.tasks_unscheduled }}，
          不参与 {{ draft.coverage.tasks_excluded }}。
        </p>
        <div class="page-actions" v-if="draft.status === 'draft'">
          <button type="button" :disabled="accepting" @click="accept">
            {{ accepting ? '接受中…' : '接受为当前计划' }}
          </button>
        </div>
      </section>

      <section v-if="draft.conflicts.length" class="panel" aria-label="冲突与风险">
        <div class="section-heading">
          <div><p class="eyebrow">检查结果</p><h2>冲突与风险</h2></div>
          <span class="count-badge">确定冲突 {{ blockingConflicts.length }} · 风险 {{ risks.length }}</span>
        </div>
        <ul class="conflict-list">
          <li v-for="(conflict, index) in draft.conflicts" :key="index" :class="['conflict-item', conflict.severity]">
            <div class="task-title-line">
              <strong>{{ conflictTitle(conflict) }}</strong>
              <span class="status-pill">{{ conflict.severity === 'conflict' ? '确定冲突' : '风险' }}</span>
              <span v-if="conflict.estimated" class="status-pill">基于耗时估计</span>
            </div>
            <p>{{ conflict.message }}</p>
            <p class="form-help">建议：{{ conflict.suggestion }}</p>
          </li>
        </ul>
      </section>

      <section v-if="draft.gaps.length || draft.assumptions.length" class="panel two-column-layout" aria-label="待补充与假设">
        <div>
          <h2>待补充项</h2>
          <ul class="plain-list">
            <li v-for="gap in draft.gaps" :key="`${gap.code}-${gap.task_id}`">{{ gap.message }}</li>
            <li v-if="!draft.gaps.length" class="muted">没有待补充项。</li>
          </ul>
        </div>
        <div>
          <h2>本次使用的假设</h2>
          <ul class="plain-list">
            <li v-for="(item, index) in draft.assumptions" :key="index">{{ item.text }}</li>
            <li v-if="!draft.assumptions.length" class="muted">未使用任何假设。</li>
          </ul>
        </div>
      </section>

      <section class="panel" aria-label="时间块">
        <div class="section-heading">
          <div><p class="eyebrow">建议安排</p><h2>时间块</h2></div>
          <span class="count-badge">{{ draft.blocks.length }} 段 · {{ draft.blocks.reduce((sum, block) => sum + block.minutes, 0) }} 分钟</span>
        </div>
        <p v-if="!draft.blocks.length" class="compact-empty">本次没有可安排的时间块，原因见未排任务。</p>
        <div v-for="[day, blocks] in blocksByDay" :key="day" class="day-group">
          <h3>{{ day }}</h3>
          <article v-for="block in blocks" :key="block.id" class="block-row">
            <div class="block-main">
              <div class="task-title-line">
                <strong>{{ timeText(block.starts_at) }}–{{ timeText(block.ends_at) }}</strong>
                <span>{{ block.title }}</span>
                <span class="status-pill">{{ block.minutes }} 分钟</span>
                <span v-if="block.locked" class="status-pill">已锁定</span>
              </div>
              <p>{{ block.reason }}</p>
              <div class="constraint-tags">
                <span v-for="source in block.constraint_source" :key="source.kind" class="count-badge">
                  {{ constraintLabels[source.kind] ?? source.kind }}
                </span>
              </div>
            </div>
            <div v-if="draft.status === 'accepted'" class="block-actions">
              <button type="button" class="text-button" :disabled="busyBlockId === block.id" @click="toggleLock(block)">
                {{ block.locked ? '解锁' : '锁定' }}
              </button>
              <button type="button" class="text-button" @click="prepareMove(block)">调整时间</button>
              <div v-if="moveForms[block.id]" class="move-form">
                <label>开始<input v-model="moveForms[block.id].starts_at" type="datetime-local" /></label>
                <label>结束<input v-model="moveForms[block.id].ends_at" type="datetime-local" /></label>
                <div class="page-actions">
                  <button type="button" :disabled="busyBlockId === block.id" @click="saveMove(block)">保存</button>
                  <button type="button" class="text-button" @click="delete moveForms[block.id]">取消</button>
                </div>
              </div>
            </div>
          </article>
        </div>
      </section>

      <section v-if="draft.unscheduled.length" class="panel" aria-label="未排任务">
        <h2>未完整排入的任务</h2>
        <ul class="conflict-list">
          <li v-for="item in draft.unscheduled" :key="item.task_id" class="conflict-item">
            <div class="task-title-line">
              <strong>{{ item.title }}</strong>
              <span class="status-pill">{{ dispositionLabels[item.status] ?? item.status }}</span>
              <span v-if="item.remaining_minutes" class="status-pill">缺口 {{ item.remaining_minutes }} 分钟</span>
            </div>
            <p>{{ item.message }}</p>
            <p v-if="item.options.length" class="form-help">可选处理：{{ item.options.join('、') }}</p>
            <p v-if="item.status === 'excluded'" class="form-help">已完成或已取消的任务不参与排期，记录仍保留在任务列表。</p>
          </li>
        </ul>
        <div v-if="adjustOptions.length" class="inline-notice">
          调整选项：{{ adjustOptions.join('；') }}
        </div>
      </section>

      <section v-if="draft.coverage.not_checked.length || draft.coverage.notes.length" class="panel" aria-label="检查覆盖">
        <h2>检查覆盖与限制</h2>
        <ul class="plain-list">
          <li v-for="(item, index) in draft.coverage.not_checked" :key="`unchecked-${index}`">{{ item }}</li>
          <li v-for="(item, index) in draft.coverage.notes" :key="`note-${index}`" class="muted">{{ item }}</li>
        </ul>
        <p class="form-help">生成于 {{ dateTimeText(draft.created_at) }}<span v-if="draft.accepted_at">，接受于 {{ dateTimeText(draft.accepted_at) }}</span>。</p>
      </section>
    </template>
  </main>
</template>

<style scoped>
.page-actions { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }
.plan-summary { display: grid; gap: 12px; }
.meta-row { display: flex; flex-wrap: wrap; gap: 8px 18px; align-items: baseline; color: var(--muted); font-size: 13px; }
.meta-row strong { color: var(--ink); }
.conflict-list, .plain-list { margin: 0; padding: 0; list-style: none; display: grid; gap: 10px; }
.conflict-item { padding: 14px; border: 1px solid var(--line); border-left: 4px solid var(--warn-ink); border-radius: 10px; background: var(--surface-muted); }
.conflict-item.conflict { border-left-color: var(--danger); }
.conflict-item p { margin: 4px 0; }
.day-group { margin-top: 20px; }
.day-group h3 { margin: 0 0 10px; font-size: 15px; }
.block-row { display: grid; gap: 12px; padding: 14px 0; border-bottom: 1px solid var(--line); }
.block-row:last-child { border-bottom: 0; }
.block-main p { margin: 5px 0; color: var(--muted); font-size: 13px; }
.constraint-tags { display: flex; flex-wrap: wrap; gap: 6px; }
.block-actions { display: flex; flex-wrap: wrap; gap: 10px; align-items: end; }
.move-form { display: flex; flex-wrap: wrap; gap: 10px; align-items: end; }
.move-form label { display: grid; gap: 4px; color: var(--muted); font-size: 12px; }
.move-form input { padding: 7px 9px; border: 1px solid var(--line-strong); border-radius: 8px; background: var(--paper-strong); color: var(--ink); }
</style>
