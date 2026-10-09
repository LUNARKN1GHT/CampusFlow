<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import { coursesApi, scheduleApi } from '../api/business'
import { errorMessage } from '../api/client'
import { settingsApi } from '../api/settings'
import type { Course, FixedEvent, FixedEventInput, FixedEventOccurrence } from '../api/types'
import ApiFeedback from './ApiFeedback.vue'

const definitions = ref<FixedEvent[]>([])
const occurrences = ref<FixedEventOccurrence[]>([])
const courses = ref<Course[]>([])
const loading = ref(true)
const error = ref('')
const editorOpen = ref(false)
const editing = ref<FixedEvent | null>(null)
const saving = ref(false)
const formError = ref('')
const timezone = ref('Asia/Shanghai')
const weekStart = ref(startOfWeek(new Date()))
let weekInitialized = false
const form = reactive({ course_id: '', title: '', starts_at: '', ends_at: '', location: '', recurrence: 'none' as 'none' | 'weekly', repeat_until: '' })

function startOfWeek(value: Date): Date {
  const date = new Date(value.getFullYear(), value.getMonth(), value.getDate())
  const offset = (date.getDay() + 6) % 7
  date.setDate(date.getDate() - offset)
  return date
}

function dateKey(value: Date): string {
  const year = value.getFullYear()
  const month = String(value.getMonth() + 1).padStart(2, '0')
  const day = String(value.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

function workspaceInput(value: string): string {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: timezone.value, year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
  }).formatToParts(new Date(value))
  const get = (kind: Intl.DateTimeFormatPartTypes) => parts.find((part) => part.type === kind)?.value
  return `${get('year')}-${get('month')}-${get('day')}T${get('hour')}:${get('minute')}`
}

const days = computed(() => Array.from({ length: 7 }, (_, index) => {
  const date = new Date(weekStart.value)
  date.setDate(date.getDate() + index)
  const key = dateKey(date)
  return { key, label: new Intl.DateTimeFormat('zh-CN', { weekday: 'short', month: 'numeric', day: 'numeric' }).format(date), events: occurrences.value.filter((item) => workspaceInput(item.starts_at).slice(0, 10) === key) }
}))

const weekEnd = computed(() => days.value[6]?.key ?? dateKey(weekStart.value))
const courseMap = computed(() => new Map(courses.value.map((course) => [course.id, course])))

async function load() {
  loading.value = true
  error.value = ''
  try {
    timezone.value = (await settingsApi.get()).timezone
    if (!weekInitialized) {
      weekStart.value = startOfWeek(new Date(`${workspaceInput(new Date().toISOString()).slice(0, 10)}T12:00:00`))
      weekInitialized = true
    }
    ;[definitions.value, occurrences.value, courses.value] = await Promise.all([
      scheduleApi.listEvents(), scheduleApi.listOccurrences(dateKey(weekStart.value), weekEnd.value), coursesApi.list(),
    ])
  } catch (caught) {
    error.value = errorMessage(caught)
  } finally {
    loading.value = false
  }
}

function changeWeek(offset: number) {
  const next = new Date(weekStart.value)
  next.setDate(next.getDate() + offset * 7)
  weekStart.value = next
  void load()
}

function openEditor(event?: FixedEvent) {
  editing.value = event ?? null
  editorOpen.value = true
  formError.value = ''
  Object.assign(form, {
    course_id: event?.course_id?.toString() ?? '', title: event?.title ?? '',
    starts_at: event ? workspaceInput(event.starts_at) : '', ends_at: event ? workspaceInput(event.ends_at) : '',
    location: event?.location ?? '', recurrence: event?.recurrence ?? 'none', repeat_until: event?.repeat_until ?? '',
  })
}

function payload(): FixedEventInput {
  return {
    course_id: form.course_id ? Number(form.course_id) : null, title: form.title,
    starts_at: editing.value && form.starts_at === workspaceInput(editing.value.starts_at) ? editing.value.starts_at : form.starts_at,
    ends_at: editing.value && form.ends_at === workspaceInput(editing.value.ends_at) ? editing.value.ends_at : form.ends_at,
    location: form.location || null,
    recurrence: form.recurrence, repeat_until: form.recurrence === 'weekly' ? form.repeat_until || null : null,
  }
}

async function saveEvent() {
  saving.value = true
  formError.value = ''
  try {
    if (editing.value) await scheduleApi.updateEvent(editing.value.id, payload())
    else await scheduleApi.createEvent(payload())
    editorOpen.value = false
    await load()
  } catch (caught) {
    formError.value = errorMessage(caught)
  } finally {
    saving.value = false
  }
}

async function deleteEvent(event: FixedEvent) {
  if (!window.confirm(`删除固定日程“${event.title}”？它的每周重复实例也会一并移除。`)) return
  try {
    await scheduleApi.deleteEvent(event.id)
    await load()
  } catch (caught) {
    error.value = errorMessage(caught)
  }
}

function timeRange(item: FixedEventOccurrence): string {
  const formatter = new Intl.DateTimeFormat('zh-CN', { timeZone: timezone.value, hour: '2-digit', minute: '2-digit', hourCycle: 'h23' })
  return `${formatter.format(new Date(item.starts_at))}–${formatter.format(new Date(item.ends_at))}`
}

onMounted(load)
</script>

<template>
  <section class="panel schedule-panel">
    <div class="section-heading">
      <div><p class="eyebrow">固定日程 · {{ timezone }}</p><h2>本周时间表</h2></div>
      <div class="schedule-actions"><button type="button" class="button-secondary" @click="changeWeek(-1)">上一周</button><button type="button" class="button-secondary" @click="changeWeek(1)">下一周</button><button type="button" @click="openEditor()">添加日程</button></div>
    </div>
    <p class="form-help schedule-note">这里展示不可移动的时间区间；任务 DDL 始终保留在上方任务列表，不会因编辑日程而改变。</p>
    <ApiFeedback v-if="error" :message="error" :busy="loading" @retry="load" />
    <p v-else-if="loading" class="muted">正在展开本周日程…</p>
    <div v-else class="week-grid">
      <div v-for="day in days" :key="day.key" class="week-day"><strong>{{ day.label }}</strong><div v-if="day.events.length" class="day-events"><article v-for="item in day.events" :key="`${item.source_event_id}-${item.starts_at}`"><time>{{ timeRange(item) }}</time><b>{{ item.title }}</b><span>{{ courseMap.get(item.course_id ?? -1)?.name || item.location || '固定安排' }}</span></article></div><span v-else class="day-empty">无固定安排</span></div>
    </div>

    <div v-if="definitions.length" class="definition-list"><h3 class="subheading">重复规则与编辑</h3><div><article v-for="event in definitions" :key="event.id"><span><strong>{{ event.title }}</strong><small>{{ event.recurrence === 'weekly' ? `每周重复至 ${event.repeat_until}` : '不重复' }}</small></span><span><button type="button" class="text-button" @click="openEditor(event)">编辑</button><button type="button" class="danger-button" @click="deleteEvent(event)">删除</button></span></article></div></div>
  </section>

  <section v-if="editorOpen" class="panel editor-panel">
    <div class="section-heading"><div><p class="eyebrow">固定时间区间</p><h2>{{ editing ? '编辑日程' : '添加日程' }}</h2></div><button type="button" class="text-button" @click="editorOpen = false">取消</button></div>
    <form class="stack-form" @submit.prevent="saveEvent">
      <div class="form-grid"><label>标题<input v-model.trim="form.title" required maxlength="300" /></label><label>关联课程<select v-model="form.course_id"><option value="">不关联课程</option><option v-for="course in courses" :key="course.id" :value="course.id">{{ course.name }} · {{ course.class_name || '未分班' }}</option></select></label></div>
      <div class="form-grid"><label>开始时间<input v-model="form.starts_at" type="datetime-local" required /></label><label>结束时间<input v-model="form.ends_at" type="datetime-local" required /></label></div>
      <p class="form-help">输入按工作空间 {{ timezone }} 解释。夏令时缺失或重复时刻会明确报错；时区设置不会改写已保存日程的实际时间点。</p>
      <div class="form-grid form-grid-3"><label>地点<input v-model.trim="form.location" maxlength="200" /></label><label>重复<select v-model="form.recurrence"><option value="none">不重复</option><option value="weekly">每周重复</option></select></label><label>重复截止日<input v-model="form.repeat_until" type="date" :required="form.recurrence === 'weekly'" :disabled="form.recurrence !== 'weekly'" /></label></div>
      <p v-if="formError" class="form-error" role="alert">{{ formError }}</p><button type="submit" :disabled="saving">{{ saving ? '正在保存…' : '保存日程' }}</button>
    </form>
  </section>
</template>
