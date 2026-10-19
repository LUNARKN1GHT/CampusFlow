<script setup lang="ts">
/*
 * 提醒页：按全部／未读／已读查看提醒并标为已读或忽略，同时维护默认与任务单独提醒规则。
 * 流程：读取列表时后端先同步生成提醒；修改规则后重算待发送记录，发送过或已忽略的历史保留。
 * 约束：提醒只用于提示，不改变任务 DDL、核对状态或计划；没有提醒也不代表没有待办或冲突。
 */
import { computed, onMounted, reactive, ref } from 'vue'

import { remindersApi, tasksApi } from '../api/business'
import { errorMessage } from '../api/client'
import type {
  Reminder,
  ReminderKind,
  ReminderRule,
  ReminderRuleInput,
  ReminderStatus,
  ReminderSummary,
  Task,
} from '../api/types'
import ApiFeedback from '../components/ApiFeedback.vue'

type ReminderView = 'all' | 'unread' | 'read'

const NO_REMINDER_NOTE = '没有提醒不代表没有待办或冲突，请以任务列表与冲突检查为准。'

const view = ref<ReminderView>('all')
const reminders = ref<Reminder[]>([])
const summary = ref<ReminderSummary | null>(null)
const rules = ref<ReminderRule[]>([])
const tasks = ref<Task[]>([])
const loading = ref(true)
const listLoading = ref(false)
const loadError = ref('')
const listError = ref('')
const actionBusy = ref<number | null>(null)

const defaultForm = reactive({
  advance_minutes: 1440,
  repeat_minutes: '',
  paused: false,
  enabled: true,
})
const defaultSaving = ref(false)
const defaultError = ref('')
const defaultMessage = ref('')

const ruleForm = reactive({
  task_id: '',
  advance_minutes: 1440,
  repeat_minutes: '',
  paused: false,
  enabled: true,
})
const editingRuleId = ref<number | null>(null)
const ruleSaving = ref(false)
const ruleError = ref('')
const ruleMessage = ref('')
const deletingRuleId = ref<number | null>(null)

const statusLabels: Record<ReminderStatus, string> = {
  pending: '待发送',
  sent: '已发送',
  dismissed: '已忽略',
  cancelled: '已撤销',
}
const kindLabels: Record<ReminderKind, string> = { deadline: '截止提醒', review: '待核对提醒' }

const defaultRule = computed(() => rules.value.find((rule) => rule.is_default) ?? null)
const taskRules = computed(() => rules.value.filter((rule) => !rule.is_default))
const taskMap = computed(() => new Map(tasks.value.map((task) => [task.id, task])))
const tasksWithRule = computed(() => new Set(taskRules.value.map((rule) => rule.task_id)))
const tasksWithoutRule = computed(() => tasks.value.filter((task) => !tasksWithRule.value.has(task.id)))

function listFilter(): { unreadOnly?: boolean; status?: ReminderStatus } {
  if (view.value === 'unread') return { unreadOnly: true }
  if (view.value === 'read') return { status: 'sent' }
  return {}
}

// 后端只有 unread_only 和 status 两种筛选；"已读"先取已发送记录，再前端排除未读，
// 避免把待发送、已撤销等非发送状态误算成已读。
function applyReminders(items: Reminder[]): void {
  reminders.value = view.value === 'read'
    ? items.filter((item) => item.read_at !== null)
    : items
}

async function refreshReminders(): Promise<void> {
  listLoading.value = true
  listError.value = ''
  try {
    const [items, overview] = await Promise.all([
      remindersApi.list(listFilter()),
      remindersApi.summary(),
    ])
    applyReminders(items)
    summary.value = overview
  } catch (caught) {
    listError.value = errorMessage(caught)
  } finally {
    listLoading.value = false
  }
}

async function load(): Promise<void> {
  loading.value = true
  loadError.value = ''
  try {
    const [items, overview, ruleItems, taskItems] = await Promise.all([
      remindersApi.list(),
      remindersApi.summary(),
      remindersApi.listRules(),
      tasksApi.list(),
    ])
    applyReminders(items)
    summary.value = overview
    rules.value = ruleItems
    tasks.value = taskItems
    syncDefaultForm()
  } catch (caught) {
    loadError.value = errorMessage(caught)
  } finally {
    loading.value = false
  }
}

function changeView(next: ReminderView): void {
  if (view.value === next) return
  view.value = next
  void refreshReminders()
}

async function act(reminder: Reminder, action: 'read' | 'dismiss'): Promise<void> {
  actionBusy.value = reminder.id
  listError.value = ''
  try {
    await remindersApi.action(reminder.id, action)
    await refreshReminders()
  } catch (caught) {
    listError.value = errorMessage(caught)
  } finally {
    actionBusy.value = null
  }
}

// scheduled_for 是提醒窗口时间，不一定是截止时刻；date_only 时只显示日期，
// 避免把窗口计算或同步时刻误当成任务截止时刻。
function formatScheduled(reminder: Reminder): string {
  const dateOnly = reminder.payload?.date_only === true
  return new Intl.DateTimeFormat('zh-CN', dateOnly
    ? { dateStyle: 'medium' }
    : { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(reminder.scheduled_for))
}

function formatMinutes(minutes: number): string {
  if (minutes === 0) return '0 分钟'
  if (minutes % 1440 === 0) return `${minutes / 1440} 天`
  if (minutes % 60 === 0) return `${minutes / 60} 小时`
  return `${minutes} 分钟`
}

function advanceText(minutes: number): string {
  const friendly = formatMinutes(minutes)
  return friendly === `${minutes} 分钟` ? friendly : `${minutes} 分钟（${friendly}）`
}

// task_id 为空的是工作空间级提醒（如待核对汇总），不对应单个任务。
function taskTitle(taskId: number | null): string {
  if (taskId === null) return '工作空间提醒'
  return taskMap.value.get(taskId)?.title ?? `任务 #${taskId}`
}

function repeatText(rule: ReminderRule): string {
  return rule.repeat_minutes === null ? '不重复' : `每 ${advanceText(rule.repeat_minutes)}重复一次`
}

// 内置默认规则可能尚未落库（id 为空），保存时走创建；已有记录则按版本更新。
function syncDefaultForm(): void {
  const rule = defaultRule.value
  Object.assign(defaultForm, {
    advance_minutes: rule?.advance_minutes ?? 1440,
    repeat_minutes: rule?.repeat_minutes?.toString() ?? '',
    paused: rule?.paused ?? false,
    enabled: rule?.enabled ?? true,
  })
}

function resetDefaultForm(): void {
  syncDefaultForm()
  defaultError.value = ''
  defaultMessage.value = ''
}

async function reloadRules(): Promise<void> {
  rules.value = await remindersApi.listRules()
}

// 规则变化只重算待发送提醒，已发送／已忽略记录保留，任务截止与计划内容不受影响。
async function saveDefaultRule(): Promise<void> {
  defaultSaving.value = true
  defaultError.value = ''
  defaultMessage.value = ''
  try {
    const input: ReminderRuleInput = {
      advance_minutes: defaultForm.advance_minutes,
      repeat_minutes: defaultForm.repeat_minutes === '' ? null : Number(defaultForm.repeat_minutes),
      paused: defaultForm.paused,
      enabled: defaultForm.enabled,
    }
    const current = defaultRule.value
    if (current === null || current.id === null) {
      await remindersApi.createRule({ task_id: null, ...input })
    } else {
      await remindersApi.updateRule(current.id, input)
    }
    await reloadRules()
    syncDefaultForm()
    await refreshReminders()
    defaultMessage.value = '默认提醒规则已保存，新的待发送提醒会按当前规则重新生成。'
  } catch (caught) {
    defaultError.value = errorMessage(caught)
  } finally {
    defaultSaving.value = false
  }
}

// 新规则继承默认规则的提前量，但不继承重复间隔，避免无意中开启重复提醒。
function resetRuleForm(): void {
  Object.assign(ruleForm, {
    task_id: '',
    advance_minutes: defaultRule.value?.advance_minutes ?? 1440,
    repeat_minutes: '',
    paused: false,
    enabled: true,
  })
  editingRuleId.value = null
  ruleError.value = ''
}

function editRule(rule: ReminderRule): void {
  if (rule.id === null || rule.task_id === null) return
  editingRuleId.value = rule.id
  Object.assign(ruleForm, {
    task_id: rule.task_id.toString(),
    advance_minutes: rule.advance_minutes,
    repeat_minutes: rule.repeat_minutes?.toString() ?? '',
    paused: rule.paused,
    enabled: rule.enabled,
  })
  ruleError.value = ''
  ruleMessage.value = ''
}

async function saveTaskRule(): Promise<void> {
  if (editingRuleId.value === null && !ruleForm.task_id) {
    ruleError.value = '请选择要设置单独规则的任务。'
    return
  }
  ruleSaving.value = true
  ruleError.value = ''
  ruleMessage.value = ''
  try {
    const input: ReminderRuleInput = {
      advance_minutes: ruleForm.advance_minutes,
      repeat_minutes: ruleForm.repeat_minutes === '' ? null : Number(ruleForm.repeat_minutes),
      paused: ruleForm.paused,
      enabled: ruleForm.enabled,
    }
    if (editingRuleId.value === null) {
      await remindersApi.createRule({ task_id: Number(ruleForm.task_id), ...input })
      ruleMessage.value = '任务提醒规则已创建。'
    } else {
      await remindersApi.updateRule(editingRuleId.value, input)
      ruleMessage.value = '任务提醒规则已更新，待发送提醒已按新规则重算。'
    }
    await reloadRules()
    resetRuleForm()
    await refreshReminders()
  } catch (caught) {
    ruleError.value = errorMessage(caught)
  } finally {
    ruleSaving.value = false
  }
}

// 规则更新接口要求提交整组字段，因此保留原有提前量与重复间隔，只翻转暂停状态。
async function toggleRulePaused(rule: ReminderRule): Promise<void> {
  if (rule.id === null) return
  ruleError.value = ''
  ruleMessage.value = ''
  try {
    await remindersApi.updateRule(rule.id, {
      advance_minutes: rule.advance_minutes,
      repeat_minutes: rule.repeat_minutes,
      paused: !rule.paused,
      enabled: rule.enabled,
    })
    await reloadRules()
    await refreshReminders()
    ruleMessage.value = rule.paused ? '任务提醒规则已恢复。' : '任务提醒规则已暂停，恢复前不再生成新提醒。'
  } catch (caught) {
    ruleError.value = errorMessage(caught)
  }
}

async function removeRule(rule: ReminderRule): Promise<void> {
  if (rule.id === null) return
  deletingRuleId.value = rule.id
  ruleError.value = ''
  ruleMessage.value = ''
  try {
    await remindersApi.deleteRule(rule.id)
    await reloadRules()
    if (editingRuleId.value === rule.id) resetRuleForm()
    await refreshReminders()
    ruleMessage.value = '任务提醒规则已删除，该任务回退使用默认规则。'
  } catch (caught) {
    ruleError.value = errorMessage(caught)
  } finally {
    deletingRuleId.value = null
  }
}

onMounted(load)
</script>

<template>
  <main class="page-content">
    <div class="page-heading">
      <p class="eyebrow">提醒与跟进</p>
      <h1>提醒</h1>
      <p class="intro">集中查看应用内提醒，调整提前量、重复与暂停；没有提醒不代表没有待办或冲突。</p>
      <p class="form-help">提醒只用于提示，不会改变任务 DDL、核对状态或计划时间块。时间按工作空间时区生成，这里按本机时区显示。</p>
    </div>

    <ApiFeedback v-if="loadError" :message="loadError" :busy="loading" @retry="load" />
    <template v-else>
      <section class="panel filter-bar" aria-label="提醒筛选">
        <div class="segmented" role="group" aria-label="提醒阅读状态">
          <button type="button" class="segmented-item" :class="{ 'is-active': view === 'all' }" :aria-pressed="view === 'all'" @click="changeView('all')">全部</button>
          <button type="button" class="segmented-item" :class="{ 'is-active': view === 'unread' }" :aria-pressed="view === 'unread'" @click="changeView('unread')">未读</button>
          <button type="button" class="segmented-item" :class="{ 'is-active': view === 'read' }" :aria-pressed="view === 'read'" @click="changeView('read')">已读</button>
        </div>
        <span class="count-badge">{{ reminders.length }} 条</span>
      </section>

      <section class="panel">
        <div class="section-heading">
          <div><p class="eyebrow">近期提醒</p><h2>提醒列表</h2></div>
          <button type="button" class="text-button" :disabled="listLoading" @click="refreshReminders">刷新</button>
        </div>

        <div v-if="summary" class="summary-row reminder-summary">
          <div class="summary-chip"><strong>{{ summary.unread }}</strong><span>未读</span></div>
          <div class="summary-chip"><strong>{{ summary.pending }}</strong><span>待发送</span></div>
          <div class="summary-chip"><strong>{{ summary.sent }}</strong><span>已发送</span></div>
          <div class="summary-chip"><strong>{{ summary.dismissed }}</strong><span>已忽略</span></div>
          <div class="summary-chip"><strong>{{ summary.cancelled }}</strong><span>已撤销</span></div>
        </div>
        <p class="inline-notice">{{ summary?.note || NO_REMINDER_NOTE }}</p>

        <p v-if="listError" class="form-error" role="alert">{{ listError }}</p>
        <p v-if="loading" class="muted">正在加载提醒…</p>
        <div v-else-if="reminders.length" class="task-list">
          <article v-for="reminder in reminders" :key="reminder.id" class="task-row reminder-row">
            <div class="task-main">
              <div class="task-title-line">
                <h3>{{ taskTitle(reminder.task_id) }}</h3>
                <span class="status-pill" :data-status="reminder.status">{{ statusLabels[reminder.status] }}</span>
                <span v-if="reminder.status === 'sent' && !reminder.read_at" class="status-pill unread-pill">未读</span>
                <span v-if="reminder.status === 'sent' && reminder.read_at" class="status-pill read-pill">已读</span>
                <span class="status-pill kind-pill">{{ kindLabels[reminder.kind] }}</span>
              </div>
              <p>{{ reminder.message }}</p>
              <p class="reminder-time">
                计划提醒：{{ formatScheduled(reminder) }}
                <span v-if="reminder.payload?.date_only === true">（截止仅有日期，不表示具体时刻）</span>
              </p>
              <RouterLink v-if="reminder.task_id !== null" class="reminder-link" :to="`/tasks?task=${reminder.task_id}`">查看任务</RouterLink>
            </div>
            <div class="reminder-actions">
              <button
                v-if="reminder.status === 'sent' && !reminder.read_at"
                type="button"
                class="button-secondary"
                :disabled="actionBusy === reminder.id"
                @click="act(reminder, 'read')"
              >{{ actionBusy === reminder.id ? '处理中…' : '标为已读' }}</button>
              <button
                v-if="reminder.status === 'pending' || reminder.status === 'sent'"
                type="button"
                class="text-button"
                :disabled="actionBusy === reminder.id"
                @click="act(reminder, 'dismiss')"
              >忽略</button>
            </div>
          </article>
        </div>
        <div v-else class="compact-empty">当前筛选下没有提醒。没有提醒不代表没有待办或冲突，请以任务列表与冲突检查为准。</div>
      </section>

      <section class="panel" aria-label="默认提醒规则">
        <div class="section-heading">
          <div><p class="eyebrow">默认策略</p><h2>所有任务的提醒规则</h2></div>
          <span class="count-badge">{{ defaultRule?.builtin ? '内置默认' : `版本 ${defaultRule?.version ?? 1}` }}</span>
        </div>
        <p class="form-help">未单独设置的任务都使用这套规则。停用默认规则后，没有单独规则的任务不再生成提醒；暂停则保留规则但暂时不生成。</p>
        <form class="stack-form" @submit.prevent="saveDefaultRule">
          <div class="form-grid">
            <label>默认提前量（分钟）<input v-model.number="defaultForm.advance_minutes" type="number" min="0" max="10080" required /></label>
            <label>默认重复间隔（分钟，可留空）<input v-model="defaultForm.repeat_minutes" type="number" min="1" max="10080" placeholder="留空表示不重复" /></label>
          </div>
          <div class="form-grid">
            <label class="switch-field"><input v-model="defaultForm.paused" type="checkbox" /> 暂停默认提醒</label>
            <label class="switch-field"><input v-model="defaultForm.enabled" type="checkbox" /> 启用默认规则</label>
          </div>
          <p class="form-help">提前量范围 0–10080 分钟。只有日期的截止不会补造具体时刻，提醒也会按日期精度提示。</p>
          <p v-if="defaultError" class="form-error" role="alert">{{ defaultError }}</p>
          <p v-if="defaultMessage" class="success-message" role="status">{{ defaultMessage }}</p>
          <div class="reminder-actions">
            <button type="submit" :disabled="defaultSaving">{{ defaultSaving ? '正在保存…' : '保存默认规则' }}</button>
            <button type="button" class="text-button" :disabled="defaultSaving" @click="resetDefaultForm">重置</button>
          </div>
        </form>
      </section>

      <section class="panel" aria-label="任务提醒规则">
        <div class="section-heading">
          <div><p class="eyebrow">单独规则</p><h2>任务提醒规则</h2></div>
          <span class="count-badge">{{ taskRules.length }} 条</span>
        </div>
        <p class="form-help">单独规则优先于默认规则；删除后该任务回退使用默认规则，停用或暂停则不生成提醒。已完成或已取消的任务不会继续提醒。</p>
        <p v-if="ruleError" class="form-error" role="alert">{{ ruleError }}</p>
        <p v-if="ruleMessage" class="success-message" role="status">{{ ruleMessage }}</p>

        <div v-if="taskRules.length" class="rule-grid">
          <article v-for="rule in taskRules" :key="rule.id ?? rule.task_id ?? 'rule'" class="rule-card">
            <div class="task-title-line">
              <h3>{{ taskTitle(rule.task_id) }}</h3>
              <span v-if="rule.paused" class="status-pill" data-status="dismissed">已暂停</span>
              <span v-if="!rule.enabled" class="status-pill" data-status="cancelled">已停用</span>
            </div>
            <p class="muted small">提前 {{ advanceText(rule.advance_minutes) }} · {{ repeatText(rule) }}</p>
            <p class="form-help">规则版本 {{ rule.version }}{{ rule.enabled ? '' : ' · 当前停用，不生成提醒' }}</p>
            <div class="reminder-actions">
              <RouterLink v-if="rule.task_id !== null" class="reminder-link" :to="`/tasks?task=${rule.task_id}`">查看任务</RouterLink>
              <button type="button" class="text-button" @click="editRule(rule)">编辑</button>
              <button type="button" class="text-button" @click="toggleRulePaused(rule)">{{ rule.paused ? '恢复' : '暂停' }}</button>
              <button type="button" class="danger-button" :disabled="deletingRuleId === rule.id" @click="removeRule(rule)">{{ deletingRuleId === rule.id ? '删除中…' : '删除' }}</button>
            </div>
          </article>
        </div>
        <div v-else class="compact-empty">还没有单独的任务提醒规则，所有任务都使用默认规则。</div>

        <div class="section-heading rule-form-heading">
          <div><p class="eyebrow">{{ editingRuleId === null ? '新建规则' : '编辑规则' }}</p><h3 class="subheading">{{ editingRuleId === null ? '为任务设置单独提醒' : taskTitle(Number(ruleForm.task_id) || null) }}</h3></div>
          <button v-if="editingRuleId !== null" type="button" class="text-button" @click="resetRuleForm">取消编辑</button>
        </div>
        <form v-if="tasks.length" class="stack-form" @submit.prevent="saveTaskRule">
          <!-- 新建时只列出还没有单独规则的任务；已有规则的通过上方卡片编辑。 -->
          <label>任务<select v-model="ruleForm.task_id" :disabled="editingRuleId !== null" required><option value="" disabled>请选择任务</option><option v-for="task in (editingRuleId === null ? tasksWithoutRule : tasks)" :key="task.id" :value="task.id">{{ task.title }}{{ task.due_date ? ` · ${task.due_date}` : '' }}</option></select></label>
          <div class="form-grid">
            <label>任务提前量（分钟）<input v-model.number="ruleForm.advance_minutes" type="number" min="0" max="10080" required /></label>
            <label>任务重复间隔（分钟，可留空）<input v-model="ruleForm.repeat_minutes" type="number" min="1" max="10080" placeholder="留空表示不重复" /></label>
          </div>
          <div class="form-grid">
            <label class="switch-field"><input v-model="ruleForm.paused" type="checkbox" /> 暂停这条规则</label>
            <label class="switch-field"><input v-model="ruleForm.enabled" type="checkbox" /> 启用这条规则</label>
          </div>
          <p v-if="editingRuleId === null && !tasksWithoutRule.length" class="form-help">所有任务都已经有单独规则，可直接在上方编辑。</p>
          <button type="submit" :disabled="ruleSaving || (editingRuleId === null && !tasksWithoutRule.length)">{{ ruleSaving ? '正在保存…' : editingRuleId === null ? '创建任务规则' : '保存任务规则' }}</button>
        </form>
        <div v-else class="compact-empty">还没有任务，请先在任务页添加任务后再设置单独提醒规则。</div>
      </section>
    </template>
  </main>
</template>
