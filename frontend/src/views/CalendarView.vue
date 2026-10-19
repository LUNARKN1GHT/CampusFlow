<script setup lang="ts">
/*
 * 外部日历页：选择已授权连接，从显式任务或已接受计划生成写入草案，再逐项确认并执行。
 * 流程：预览（不调用供应商）→ 勾选条目确认（15 分钟确认指纹）→ 执行写入 → 逐项展示成功／失败／待核实。
 * 约束：查询、预览和关闭页面都不创建外部事件；失败或未知不得显示为已同步，重试不会重复已成功项。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { tasksApi } from '../api/business'
import { calendarApi } from '../api/calendar'
import type {
  CalendarConnection,
  CalendarDraft,
  CalendarOperation,
  DraftItem,
  ExecutionResult,
  SyncDifferences,
} from '../api/calendar'
import { errorMessage } from '../api/client'
import { plansApi } from '../api/plans'
import type { PlanDraft } from '../api/plans'
import type { Task } from '../api/types'
import ApiFeedback from '../components/ApiFeedback.vue'

const connections = ref<CalendarConnection[]>([])
const tasks = ref<Task[]>([])
const plans = ref<PlanDraft[]>([])
const loading = ref(true)
const loadError = ref('')
const actionError = ref('')
const actionMessage = ref('')
const busy = ref('')

const selectedConnectionId = ref<number | ''>('')
const mode = ref<'tasks' | 'plan'>('tasks')
const selectedTaskIds = ref<number[]>([])
const selectedPlanId = ref<number | ''>('')
const taskSearch = ref('')

const draft = ref<CalendarDraft | null>(null)
const selectedItemIds = ref<number[]>([])
const execution = ref<ExecutionResult | null>(null)
const differences = ref<SyncDifferences | null>(null)
const connectionWaiting = ref(false)
// OAuth 弹窗无法回传结果，只能轮询连接列表；最多等待 180 秒，见到可用连接即停止。
let connectionTimer: number | null = null

const statusLabels: Record<string, string> = {
  active: '已连接',
  revoked: '已撤销',
  expired: '已过期',
  draft: '待确认',
  confirmed: '已确认',
  executing: '执行中',
  completed: '已完成',
  partial: '部分成功',
  failed: '未成功',
  unknown: '待核实',
  cancelled: '已取消',
  pending: '待执行',
  success: '已写入',
  skipped: '已跳过',
}
const operationLabels: Record<string, string> = {
  create: '新增',
  update: '更新',
  delete: '删除',
}
const sourceLabels: Record<string, string> = {
  task: '任务',
  plan_block: '计划时间块',
}

const activeConnections = computed(() =>
  connections.value.filter((connection) => connection.status === 'active'),
)
const activeConnection = computed(() =>
  connections.value.find((connection) => connection.id === selectedConnectionId.value) ?? null,
)
const acceptedPlans = computed(() =>
  plans.value
    .filter((plan) => plan.status === 'accepted')
    .sort((left, right) => right.range_start.localeCompare(left.range_start)),
)
// 已完成任务不写入外部日历；无截止日期的任务仍展示但不可勾选，因为外部事件需要日期。
const filteredTasks = computed(() => {
  const keyword = taskSearch.value.trim().toLowerCase()
  const base = tasks.value.filter((task) => task.progress !== 'done')
  if (!keyword) return base
  return base.filter((task) => task.title.toLowerCase().includes(keyword))
})
const selectedTaskSet = computed(() => new Set(selectedTaskIds.value))
const selectedItemSet = computed(() => new Set(selectedItemIds.value))
const canCreateDraft = computed(() => {
  if (!activeConnection.value) return false
  return mode.value === 'tasks' ? selectedTaskIds.value.length > 0 : selectedPlanId.value !== ''
})
const selectedPlan = computed(
  () => acceptedPlans.value.find((plan) => plan.id === selectedPlanId.value) ?? null,
)
// 只有待确认或已过期的草案可再次确认；已执行状态必须重新生成草案。
const draftCanConfirm = computed(
  () => draft.value?.status === 'draft' || draft.value?.status === 'expired',
)
const retryable = computed(() =>
  execution.value?.items.filter(
    (row) => row.operation?.status === 'failed' || row.operation?.status === 'unknown',
  ) ?? [],
)
const unknownOperations = computed(
  () => execution.value?.items.filter((row) => row.operation?.status === 'unknown') ?? [],
)

function resetPreview(): void {
  draft.value = null
  selectedItemIds.value = []
  execution.value = null
  actionError.value = ''
  actionMessage.value = ''
}

// 连接、模式或来源变化都会使草案内容和确认指纹失效，必须重新生成并确认。
watch([mode, selectedTaskIds, selectedPlanId, selectedConnectionId], resetPreview)
watch(selectedConnectionId, () => {
  differences.value = null
})

async function load(): Promise<void> {
  loading.value = true
  loadError.value = ''
  try {
    const [connectionItems, taskItems, planItems] = await Promise.all([
      calendarApi.listConnections(),
      tasksApi.list(),
      plansApi.list(),
    ])
    connections.value = connectionItems
    tasks.value = taskItems
    plans.value = planItems
    if (selectedConnectionId.value === '') {
      const active = connectionItems.find((item) => item.status === 'active')
      if (active) selectedConnectionId.value = active.id
    }
  } catch (caught) {
    loadError.value = errorMessage(caught)
  } finally {
    loading.value = false
  }
}

async function refreshConnections(): Promise<void> {
  try {
    connections.value = await calendarApi.listConnections()
    if (selectedConnectionId.value === '') {
      const active = connections.value.find((item) => item.status === 'active')
      if (active) selectedConnectionId.value = active.id
    }
  } catch {
    // 轮询失败不打断用户；页面仍可手动刷新。
  }
}

function stopWaiting(): void {
  connectionWaiting.value = false
  if (connectionTimer !== null) {
    window.clearInterval(connectionTimer)
    connectionTimer = null
  }
}

async function connect(): Promise<void> {
  actionError.value = ''
  actionMessage.value = ''
  busy.value = 'connect'
  stopWaiting()
  try {
    const start = await calendarApi.startOAuth()
    const popup = window.open(start.authorization_url, 'campusflow-calendar-oauth', 'width=560,height=760')
    if (!popup) {
      actionError.value = '浏览器阻止了授权窗口，请允许弹窗后重试。'
      return
    }
    connectionWaiting.value = true
    const startedAt = Date.now()
    connectionTimer = window.setInterval(async () => {
      if (Date.now() - startedAt > 180_000) {
        stopWaiting()
        actionError.value = '等待授权超时。请关闭授权窗口后重试。'
        return
      }
      await refreshConnections()
      if (activeConnections.value.length > 0) {
        stopWaiting()
        actionMessage.value = '连接已建立。可以关闭授权窗口并继续选择要写入的内容。'
      }
    }, 2000)
  } catch (caught) {
    actionError.value = errorMessage(caught)
  } finally {
    busy.value = ''
  }
}

async function revoke(connection: CalendarConnection): Promise<void> {
  const confirmed = window.confirm(
    `撤销 ${connection.account_email} 的连接后，待写入操作将不能执行；已写入外部日历的事件不会被删除。继续吗？`,
  )
  if (!confirmed) return
  actionError.value = ''
  actionMessage.value = ''
  busy.value = `revoke-${connection.id}`
  try {
    await calendarApi.revoke(connection.id)
    resetPreview()
    differences.value = null
    await refreshConnections()
    actionMessage.value = '连接已撤销；内部任务和计划保持不变。'
  } catch (caught) {
    actionError.value = errorMessage(caught)
  } finally {
    busy.value = ''
  }
}

function toggleTask(id: number): void {
  selectedTaskIds.value = selectedTaskSet.value.has(id)
    ? selectedTaskIds.value.filter((taskId) => taskId !== id)
    : [...selectedTaskIds.value, id].sort((left, right) => left - right)
}

function toggleItem(id: number): void {
  selectedItemIds.value = selectedItemSet.value.has(id)
    ? selectedItemIds.value.filter((itemId) => itemId !== id)
    : [...selectedItemIds.value, id].sort((left, right) => left - right)
}

function taskDueText(task: Task): string {
  if (!task.due_date) return '没有截止日期，不能写入外部日历'
  return task.due_time ? `${task.due_date} ${task.due_time.slice(0, 5)}` : `${task.due_date}（全天）`
}

// 授权到期与确认有效期等页面时间戳按浏览器本机时区显示，格式取可排序的 sv-SE。
function formatLocal(value: string | null): string {
  if (!value) return '—'
  return new Intl.DateTimeFormat('sv-SE', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(new Date(value))
}

// 草案条目必须按目标日历时区展示：仅日期条目若按 UTC 读可能落到前一天，不能直接用本机时区。
function formatInZone(value: string, timeZone: string): string {
  try {
    return new Intl.DateTimeFormat('sv-SE', {
      timeZone,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    }).format(new Date(value))
  } catch {
    return formatLocal(value)
  }
}

// 仅日期事件只显示目标时区下的日期并标“全天”，不展开仅用于表示全天的起止边界。
function itemTime(item: DraftItem): string {
  if (item.date_only) return `${formatInZone(item.starts_at, item.timezone).slice(0, 10)} 全天`
  return `${formatInZone(item.starts_at, item.timezone)} → ${formatInZone(item.ends_at, item.timezone)} · ${item.timezone}`
}

function statusLabel(status: string): string {
  return statusLabels[status] ?? status
}

// 预览只读内部数据、不调用供应商；条目默认全选，但此时尚未确认，也未写入。
async function createPreview(): Promise<void> {
  if (!activeConnection.value) return
  actionError.value = ''
  actionMessage.value = ''
  busy.value = 'preview'
  try {
    const source =
      mode.value === 'tasks'
        ? { kind: 'tasks' as const, task_ids: [...selectedTaskIds.value] }
        : { kind: 'plan' as const, plan_id: Number(selectedPlanId.value) }
    draft.value = await calendarApi.createDraft(activeConnection.value.id, source)
    selectedItemIds.value = draft.value.items.map((item) => item.id)
    execution.value = null
    actionMessage.value = '以下内容尚未写入。请核对后确认。'
  } catch (caught) {
    actionError.value = errorMessage(caught)
  } finally {
    busy.value = ''
  }
}

// 内容指纹来自预览，选中范围以本次提交为准；后端据此生成 15 分钟有效的确认指纹。
async function confirmDraft(): Promise<void> {
  const current = draft.value
  if (!current) return
  if (selectedItemIds.value.length === 0) {
    actionError.value = '请至少勾选一条要写入外部日历的内容。'
    return
  }
  actionError.value = ''
  actionMessage.value = ''
  busy.value = 'confirm'
  try {
    draft.value = await calendarApi.confirm(
      current.id,
      current.content_fingerprint,
      selectedItemIds.value,
    )
    actionMessage.value = '确认已生效。只有点击“执行写入”后才会调用外部日历。'
  } catch (caught) {
    actionError.value = errorMessage(caught)
  } finally {
    busy.value = ''
  }
}

// 只有点击执行才调用供应商；确认过期或内部来源变化会在任何外部调用前被拒绝。
async function executeDraft(): Promise<void> {
  const current = draft.value
  if (!current?.confirmed_fingerprint) return
  actionError.value = ''
  actionMessage.value = ''
  busy.value = 'execute'
  try {
    execution.value = await calendarApi.execute(current.id, current.confirmed_fingerprint)
    draft.value = execution.value.draft
    actionMessage.value = '执行结果已逐项返回；失败或待核实项不会显示为已同步。'
  } catch (caught) {
    actionError.value = errorMessage(caught)
  } finally {
    busy.value = ''
  }
}

// 只重试失败／未知／未执行项，已成功项不会重复写入；确认指纹过期后仍需先重新确认。
async function retryFailed(): Promise<void> {
  const current = draft.value
  if (!current?.confirmed_fingerprint) return
  actionError.value = ''
  actionMessage.value = ''
  busy.value = 'retry'
  try {
    execution.value = await calendarApi.retry(current.id, current.confirmed_fingerprint)
    draft.value = execution.value.draft
    actionMessage.value = '已只重试失败或待核实项；成功项没有重复执行。'
  } catch (caught) {
    actionError.value = errorMessage(caught)
  } finally {
    busy.value = ''
  }
}

// 对账接口只返回单条操作；重新拉取草案与全部操作，以重建逐项状态和成功／失败／未知计数。
async function rebuildExecution(draftId: number): Promise<void> {
  const [current, operations] = await Promise.all([
    calendarApi.getDraft(draftId),
    calendarApi.listOperations(draftId),
  ])
  const byItem = new Map(operations.map((operation) => [operation.item_id, operation]))
  const items = current.items.map((item) => ({ item, operation: byItem.get(item.id) ?? null }))
  execution.value = {
    draft: current,
    items,
    success: items.filter((row) => row.operation?.status === 'success').length,
    failed: items.filter((row) => row.operation?.status === 'failed').length,
    unknown: items.filter((row) => row.operation?.status === 'unknown').length,
  }
  draft.value = current
}

// 待核实表示写入超时等未知结果；先向供应商按幂等键查询，仍未确定则保持待核实。
async function reconcile(operation: CalendarOperation): Promise<void> {
  actionError.value = ''
  actionMessage.value = ''
  busy.value = `reconcile-${operation.id}`
  try {
    await calendarApi.reconcile(operation.id)
    await rebuildExecution(operation.draft_id)
    actionMessage.value = '已向供应商核对结果；仍未确定的项目继续保持待核实。'
  } catch (caught) {
    actionError.value = errorMessage(caught)
  } finally {
    busy.value = ''
  }
}

// 差异检查只读内部状态；据此生成的新草案仍要重新预览和确认，不能复用旧授权。
async function loadDifferences(): Promise<void> {
  if (!activeConnection.value) return
  actionError.value = ''
  actionMessage.value = ''
  busy.value = 'differences'
  try {
    differences.value = await calendarApi.differences(activeConnection.value.id)
  } catch (caught) {
    actionError.value = errorMessage(caught)
  } finally {
    busy.value = ''
  }
}

async function createDifferenceDraft(): Promise<void> {
  if (!activeConnection.value) return
  actionError.value = ''
  actionMessage.value = ''
  busy.value = 'difference-draft'
  try {
    draft.value = await calendarApi.differenceDraft(activeConnection.value.id)
    selectedItemIds.value = draft.value.items.map((item) => item.id)
    execution.value = null
    actionMessage.value = '变更草案已生成；确认前不会修改外部日历。'
  } catch (caught) {
    actionError.value = errorMessage(caught)
  } finally {
    busy.value = ''
  }
}

onMounted(load)
onBeforeUnmount(stopWaiting)
</script>

<template>
  <main class="page-content">
    <div class="page-heading">
      <p class="eyebrow">外部连接</p>
      <h1>外部日历</h1>
      <p class="intro">先连接账户，再逐项核对并确认要写入的事件。查询和预览不会创建任何外部事件。</p>
      <p class="form-help">
        只有实际成功的结果才会显示为已写入。外部失败不会删除内部任务；撤销连接后，待写入操作立即失效。
      </p>
    </div>

    <ApiFeedback v-if="loadError" :message="loadError" :busy="loading" @retry="load" />
    <p v-if="actionError" class="form-error" role="alert">{{ actionError }}</p>
    <p v-if="actionMessage" class="success-message">{{ actionMessage }}</p>

    <template v-if="!loadError">
      <section class="panel stack-form" aria-label="日历连接">
        <div class="section-heading">
          <div><p class="eyebrow">第一步</p><h2>连接账户</h2></div>
          <span class="count-badge">{{ activeConnections.length }} 个可用连接</span>
        </div>
        <p class="form-help">
          授权由供应商完成；访问令牌只保存在后端并加密，页面永远不会获得 refresh token。连接范围限定为应用自建的专用日历。
        </p>

        <div v-if="connections.length" class="calendar-connection-list">
          <article
            v-for="connection in connections"
            :key="connection.id"
            class="calendar-connection"
            :class="{ 'is-selected': connection.id === selectedConnectionId }"
          >
            <label class="calendar-connection-main">
              <input
                v-if="connection.status === 'active'"
                v-model="selectedConnectionId"
                type="radio"
                :value="connection.id"
              />
              <span>
                <strong>{{ connection.account_email }}</strong>
                <small>{{ connection.provider }} · 日历 {{ connection.calendar_id }}</small>
                <small>状态：{{ statusLabel(connection.status) }}<template v-if="connection.token_expires_at"> · 授权到期 {{ formatLocal(connection.token_expires_at) }}</template></small>
              </span>
            </label>
            <button
              v-if="connection.status === 'active'"
              type="button"
              class="danger-button"
              :disabled="busy === `revoke-${connection.id}`"
              @click="revoke(connection)"
            >
              {{ busy === `revoke-${connection.id}` ? '撤销中…' : '撤销连接' }}
            </button>
            <span v-else class="status-pill">{{ statusLabel(connection.status) }}</span>
          </article>
        </div>
        <div v-else class="compact-empty">还没有连接外部日历。</div>

        <div class="calendar-actions">
          <button type="button" :disabled="busy === 'connect'" @click="connect">
            {{ busy === 'connect' ? '正在准备授权…' : '连接外部日历' }}
          </button>
          <button type="button" class="button-secondary" :disabled="connectionWaiting" @click="refreshConnections">
            刷新连接状态
          </button>
          <span v-if="connectionWaiting" class="muted">已打开授权窗口，完成后此页会自动刷新。</span>
        </div>
      </section>

      <section v-if="activeConnection" class="panel stack-form" aria-label="选择写入内容">
        <div class="section-heading">
          <div><p class="eyebrow">第二步</p><h2>选择来源</h2></div>
          <span class="count-badge">不会自动写入</span>
        </div>
        <div class="segmented" role="group" aria-label="来源模式">
          <button type="button" class="segmented-item" :class="{ 'is-active': mode === 'tasks' }" :aria-pressed="mode === 'tasks'" @click="mode = 'tasks'">所选任务</button>
          <button type="button" class="segmented-item" :class="{ 'is-active': mode === 'plan' }" :aria-pressed="mode === 'plan'" @click="mode = 'plan'">已接受计划</button>
        </div>

        <template v-if="mode === 'tasks'">
          <label>搜索任务<input v-model="taskSearch" type="search" placeholder="按任务标题查找" /></label>
          <p class="form-help">只写入显式勾选的任务；没有截止日期的任务会被排除。截止时刻按 15 分钟显示窗口写入，不改变任务原始 DDL。</p>
          <div v-if="filteredTasks.length" class="export-select-list">
            <label
              v-for="task in filteredTasks"
              :key="task.id"
              class="export-select-row"
              :class="{ 'is-selected': selectedTaskSet.has(task.id) }"
            >
              <input
                type="checkbox"
                :checked="selectedTaskSet.has(task.id)"
                :disabled="!task.due_date"
                @change="toggleTask(task.id)"
              />
              <span class="export-select-main">
                <span class="task-title-line"><strong>{{ task.title }}</strong></span>
                <small>{{ taskDueText(task) }}</small>
              </span>
            </label>
          </div>
          <div v-else class="compact-empty">没有符合条件的任务。</div>
        </template>

        <template v-else>
          <label>
            已接受计划
            <select v-model="selectedPlanId">
              <option value="">请选择一份当前计划</option>
              <option v-for="plan in acceptedPlans" :key="plan.id" :value="plan.id">
                #{{ plan.id }} · v{{ plan.version }} · {{ plan.range_start }} 至 {{ plan.range_end }}
              </option>
            </select>
          </label>
          <p class="form-help">
            只接受当前已接受计划；草案不能被写入。选择后会写入计划中的学习时间块，不包含提交缓冲。
            <template v-if="selectedPlan">当前：计划 #{{ selectedPlan.id }} 版本 v{{ selectedPlan.version }}。</template>
          </p>
          <div v-if="!acceptedPlans.length" class="compact-empty">还没有已接受计划，请先在计划页生成并接受。</div>
        </template>

        <div class="calendar-actions">
          <button type="button" :disabled="!canCreateDraft || busy === 'preview'" @click="createPreview">
            {{ busy === 'preview' ? '正在生成预览…' : '生成写入预览' }}
          </button>
        </div>
      </section>

      <section v-if="activeConnection" class="panel stack-form" aria-label="同步差异">
        <div class="section-heading">
          <div><p class="eyebrow">变更维护</p><h2>计划与通知变化</h2></div>
          <span class="count-badge">需重新确认</span>
        </div>
        <p class="form-help">
          检查已写入外部日历的任务或计划块是否在内部发生变化。变化只会生成新草案，不会自动复用旧授权。
        </p>
        <div class="calendar-actions">
          <button type="button" class="button-secondary" :disabled="busy === 'differences'" @click="loadDifferences">
            {{ busy === 'differences' ? '正在检查…' : '检查同步差异' }}
          </button>
          <button
            v-if="differences?.differences.length && differences.can_create_draft"
            type="button"
            :disabled="busy === 'difference-draft'"
            @click="createDifferenceDraft"
          >
            {{ busy === 'difference-draft' ? '正在生成…' : '生成更新草案' }}
          </button>
        </div>
        <p v-if="differences" class="inline-notice">{{ differences.note }}</p>
        <div v-if="differences?.differences.length" class="calendar-difference-list">
          <article v-for="difference in differences.differences" :key="`${difference.source_type}-${difference.source_id}`">
            <strong>{{ operationLabels[difference.operation_type] }} · {{ difference.title }}</strong>
            <small>{{ sourceLabels[difference.source_type] }} #{{ difference.source_id }} · {{ difference.reason }}</small>
            <small>{{ formatInZone(difference.starts_at, difference.timezone) }} → {{ formatInZone(difference.ends_at, difference.timezone) }}</small>
          </article>
        </div>
        <div v-else-if="differences" class="compact-empty">没有需要同步的外部变更。</div>
      </section>

      <section v-if="draft" class="panel stack-form" aria-label="写入预览">
        <div class="section-heading">
          <div><p class="eyebrow">第三步</p><h2>逐项确认</h2></div>
          <span class="status-pill">{{ statusLabel(draft.status) }}</span>
        </div>
        <p class="form-help">
          勾选项就是确认范围；未展示或未勾选的内容不会被写入。{{ draft.confirmed_at ? `本次确认在 ${formatLocal(draft.expires_at)} 前有效。` : '确认后 15 分钟内有效；当前尚未确认。' }}
        </p>

        <!-- 未纳入与警告由后端随草案返回，必须原样展示，否则用户会误以为全部来源已覆盖。 -->
        <div v-if="draft.excluded.length" class="calendar-notice-list">
          <p v-for="item in draft.excluded" :key="`excluded-${item.source_type}-${item.source_id}`" class="inline-notice">
            未纳入：{{ item.title || item.source_id }} — {{ item.message }}
          </p>
        </div>
        <div v-if="draft.warnings.length" class="calendar-notice-list">
          <p v-for="(warning, index) in draft.warnings" :key="`warning-${index}`" class="inline-notice">
            {{ warning.message }}
          </p>
        </div>

        <div class="calendar-item-list">
          <label
            v-for="item in draft.items"
            :key="item.id"
            class="export-select-row"
            :class="{ 'is-selected': selectedItemSet.has(item.id) }"
          >
            <input
              type="checkbox"
              :checked="selectedItemSet.has(item.id)"
              :disabled="draft.status !== 'draft' && draft.status !== 'expired'"
              @change="toggleItem(item.id)"
            />
            <span class="export-select-main">
              <span class="task-title-line">
                <strong>{{ item.title }}</strong>
                <span class="status-pill">{{ operationLabels[item.operation_type] }}</span>
              </span>
              <small>{{ sourceLabels[item.source_type] }} #{{ item.source_id }} · {{ itemTime(item) }}</small>
              <small v-if="item.external_event_id">外部事件：{{ item.external_event_id }}</small>
            </span>
          </label>
        </div>

        <div class="calendar-actions">
          <button
            v-if="draftCanConfirm"
            type="button"
            :disabled="busy === 'confirm' || selectedItemIds.length === 0"
            @click="confirmDraft"
          >
            {{ busy === 'confirm' ? '正在确认…' : `确认 ${selectedItemIds.length} 项` }}
          </button>
          <button
            v-if="draft.status === 'confirmed' && draft.confirmed_fingerprint"
            type="button"
            :disabled="busy === 'execute'"
            @click="executeDraft"
          >
            {{ busy === 'execute' ? '正在写入…' : '执行写入' }}
          </button>
          <button
            v-if="retryable.length && draft.confirmed_fingerprint"
            type="button"
            class="button-secondary"
            :disabled="busy === 'retry'"
            @click="retryFailed"
          >
            {{ busy === 'retry' ? '正在重试…' : `只重试失败项（${retryable.length}）` }}
          </button>
        </div>
        <p class="form-help">取消本页或关闭窗口不会创建外部事件；已确认内容在过期或内部变化后必须重新确认。</p>
      </section>

      <section v-if="execution" class="panel stack-form" aria-label="执行结果">
        <div class="section-heading">
          <div><p class="eyebrow">执行结果</p><h2>逐项状态</h2></div>
          <span class="count-badge">{{ execution.success }} 成功 · {{ execution.failed }} 失败 · {{ execution.unknown }} 待核实</span>
        </div>
        <p class="form-help">只有“已写入”表示外部操作成功。失败或待核实项不会显示为已同步；内部任务始终保留。</p>

        <div class="calendar-result-list">
          <article
            v-for="row in execution.items"
            :key="row.item.id"
            class="calendar-result"
            :class="row.operation ? `is-${row.operation.status}` : ''"
          >
            <div>
              <strong>{{ row.item.title }}</strong>
              <small>{{ operationLabels[row.item.operation_type] }} · {{ sourceLabels[row.item.source_type] }} #{{ row.item.source_id }}</small>
            </div>
            <span class="status-pill">{{ row.operation ? statusLabel(row.operation.status) : '未执行' }}</span>
            <p v-if="row.operation?.error_message" class="form-error">{{ row.operation.error_message }}</p>
            <small v-if="row.operation?.external_event_id">外部事件：{{ row.operation.external_event_id }}<template v-if="row.operation.attempt > 1"> · 第 {{ row.operation.attempt }} 次尝试</template></small>
            <button
              v-if="row.operation?.status === 'unknown'"
              type="button"
              class="button-secondary"
              :disabled="busy === `reconcile-${row.operation.id}`"
              @click="reconcile(row.operation)"
            >
              {{ busy === `reconcile-${row.operation.id}` ? '正在核对…' : '核对未知结果' }}
            </button>
          </article>
        </div>

        <p v-if="unknownOperations.length" class="inline-notice">
          有 {{ unknownOperations.length }} 项结果待核实。重试前会先按幂等键向供应商查询，不会直接重复创建。
        </p>
        <div v-if="execution.failed || execution.unknown" class="calendar-actions">
          <button
            v-if="draft?.confirmed_fingerprint"
            type="button"
            class="button-secondary"
            :disabled="busy === 'retry'"
            @click="retryFailed"
          >
            {{ busy === 'retry' ? '正在重试…' : '只重试失败或待核实项' }}
          </button>
        </div>
      </section>
    </template>
  </main>
</template>

<style scoped>
.calendar-connection-list,
.calendar-item-list,
.calendar-result-list,
.calendar-difference-list,
.calendar-notice-list {
  display: grid;
  gap: 10px;
}
.calendar-connection {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 14px;
  border: 1px solid var(--line);
  border-radius: 12px;
  background: var(--surface-muted);
}
.calendar-connection.is-selected {
  border-color: var(--brand-border);
  background: var(--brand-selected);
}
.calendar-connection-main {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  min-width: 0;
}
.calendar-connection-main span {
  display: grid;
  gap: 3px;
}
.calendar-connection-main small {
  color: var(--muted);
  font-size: 12px;
  word-break: break-word;
}
.calendar-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
}
.calendar-item-list {
  max-height: 460px;
  overflow-y: auto;
  padding-right: 4px;
}
.calendar-difference-list article,
.calendar-result {
  display: grid;
  gap: 4px;
  padding: 12px 14px;
  border: 1px solid var(--line);
  border-radius: 12px;
  background: var(--surface-muted);
}
.calendar-difference-list small,
.calendar-result small {
  color: var(--muted);
  font-size: 12px;
}
.calendar-result {
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: start;
}
.calendar-result p,
.calendar-result small,
.calendar-result button {
  grid-column: 1 / -1;
}
.calendar-result.is-success {
  border-color: var(--brand-border);
}
.calendar-result.is-failed,
.calendar-result.is-unknown {
  border-color: var(--danger-border);
}
.calendar-notice-list {
  gap: 6px;
}
.calendar-notice-list .inline-notice {
  margin: 0;
}
@media (max-width: 720px) {
  .calendar-connection {
    display: grid;
  }
  .calendar-result {
    grid-template-columns: 1fr;
  }
}
</style>
