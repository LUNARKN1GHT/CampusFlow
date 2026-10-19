<script setup lang="ts">
/*
 * 设置页：维护工作空间时区、每日／每周学习容量、休息与提交缓冲，以及每周可用时段。
 * 偏好只影响后续生成或重排计划时的时间解释与容量，不改写已存日程、任务 DDL 或既有计划。
 * 保存后服务端会按新的工作空间时区重算提醒窗口；界面主题保存在本机浏览器，不写入工作空间。
 */
import { onMounted, reactive, ref } from 'vue'

import { scheduleApi } from '../api/business'
import { errorMessage } from '../api/client'
import { settingsApi } from '../api/settings'
import type { AvailabilitySlot } from '../api/types'
import ApiFeedback from '../components/ApiFeedback.vue'
import { useTheme } from '../composables/useTheme'

const weekdays = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
const commonTimezones = ['Asia/Shanghai', 'Asia/Tokyo', 'Asia/Hong_Kong', 'Asia/Singapore', 'Europe/London', 'America/New_York']
const loading = ref(true)
const saving = ref(false)
const slotSaving = ref(false)
const error = ref('')
const loadError = ref('')
const message = ref('')
const slots = ref<AvailabilitySlot[]>([])
const form = reactive({ timezone: 'Asia/Shanghai', daily_capacity_minutes: 240, weekly_capacity_minutes: 1680, break_minutes: 15, buffer_minutes: 30 })
const slotForm = reactive({ day_of_week: 0, start_time: '19:00', end_time: '21:00' })
const slotError = ref('')
const { options: themeOptions, mode: themeMode, setThemeMode } = useTheme()

async function load() {
  loading.value = true
  error.value = ''
  loadError.value = ''
  try {
    const [settings, availability] = await Promise.all([settingsApi.get(), scheduleApi.listSlots()])
    Object.assign(form, settings)
    slots.value = availability
  } catch (caught) {
    loadError.value = errorMessage(caught)
  } finally {
    loading.value = false
  }
}

// 保存的是后续排期的输入；每日与每周容量独立保存，不会自动调整已录入的可用时段。
// 成功后才显示提示并用服务端返回值回填；失败只在表单内报错，已填内容保留。
async function saveSettings() {
  saving.value = true
  error.value = ''
  message.value = ''
  try {
    Object.assign(form, await settingsApi.update(form))
    message.value = '学习偏好已保存，并可从服务端重新加载。'
  } catch (caught) {
    error.value = errorMessage(caught)
  } finally {
    saving.value = false
  }
}

// 仅校验先后顺序；重叠时段与跨日由服务端拒绝，成功后按星期和开始时间重排列表。
async function addSlot() {
  slotError.value = ''
  if (slotForm.end_time <= slotForm.start_time) {
    slotError.value = '结束时间必须晚于开始时间；跨日时段请拆成两天分别录入。'
    return
  }
  slotSaving.value = true
  try {
    slots.value.push(await scheduleApi.createSlot(slotForm))
    slots.value.sort((a, b) => a.day_of_week - b.day_of_week || a.start_time.localeCompare(b.start_time))
  } catch (caught) {
    slotError.value = errorMessage(caught)
  } finally {
    slotSaving.value = false
  }
}

// 服务端删除成功后才从本地列表移除；失败保留原时段并显示错误。
async function deleteSlot(slot: AvailabilitySlot) {
  slotError.value = ''
  try {
    await scheduleApi.deleteSlot(slot.id)
    slots.value = slots.value.filter((item) => item.id !== slot.id)
  } catch (caught) {
    slotError.value = errorMessage(caught)
  }
}

onMounted(load)
</script>

<template>
  <main class="page-content">
    <div class="page-heading"><p class="eyebrow">个人配置</p><h1>设置</h1><p class="intro">这些偏好决定后续排期可使用的时间与缓冲，但不会修改课程 DDL。</p></div>
    <ApiFeedback v-if="loadError" :message="loadError" :busy="loading" @retry="load" />
    <p v-else-if="loading" class="form-help" role="status">正在加载设置…</p>
    <template v-else>
      <div class="two-column-layout settings-layout">
        <section class="panel">
          <div class="section-heading"><div><p class="eyebrow">时间基准</p><h2>时区与学习容量</h2></div></div>
          <form class="stack-form" @submit.prevent="saveSettings">
            <label>时区<input v-model.trim="form.timezone" list="timezone-options" required /><datalist id="timezone-options"><option v-for="timezone in commonTimezones" :key="timezone" :value="timezone" /></datalist></label>
            <label>每日可学习容量（分钟）<input v-model.number="form.daily_capacity_minutes" type="number" min="0" max="1440" required /></label>
            <label>每周可学习容量（分钟）<input v-model.number="form.weekly_capacity_minutes" type="number" min="0" max="10080" required /></label>
            <div class="form-grid"><label>每段休息（分钟）<input v-model.number="form.break_minutes" type="number" min="0" max="240" required /></label><label>提交缓冲（分钟）<input v-model.number="form.buffer_minutes" type="number" min="0" max="1440" required /></label></div>
            <p class="form-help">默认值：Asia/Shanghai、每日 240 分钟、每周 1680 分钟、休息 15 分钟、缓冲 30 分钟。每日与每周容量独立保存，不会自动改动可用时段或课程 DDL。</p>
            <p v-if="error" class="form-error" role="alert">{{ error }}</p><p v-if="message" class="success-message" role="status">{{ message }}</p>
            <button type="submit" :disabled="saving">{{ saving ? '正在保存…' : '保存设置' }}</button>
          </form>
        </section>

        <section class="panel">
          <div class="section-heading"><div><p class="eyebrow">每周重复</p><h2>添加可用时段</h2></div></div>
          <form class="stack-form" @submit.prevent="addSlot">
            <label>星期<select v-model.number="slotForm.day_of_week"><option v-for="(day, index) in weekdays" :key="day" :value="index">{{ day }}</option></select></label>
            <div class="form-grid"><label>开始<input v-model="slotForm.start_time" type="time" required /></label><label>结束<input v-model="slotForm.end_time" type="time" required /></label></div>
            <p class="form-help">重叠时段会被拒绝；跨日时段请拆成当天结束前与次日开始后的两段。</p>
            <p v-if="slotError" class="form-error" role="alert">{{ slotError }}</p>
            <button type="submit" :disabled="slotSaving">{{ slotSaving ? '正在保存…' : '添加时段' }}</button>
          </form>
        </section>
      </div>

      <section class="panel editor-panel">
        <div class="section-heading"><div><p class="eyebrow">真实配置</p><h2>每周可用时间</h2></div><span class="count-badge">{{ slots.length }} 段</span></div>
        <div v-if="slots.length" class="availability-grid"><article v-for="slot in slots" :key="slot.id"><span><strong>{{ weekdays[slot.day_of_week] }}</strong><small>{{ slot.start_time.slice(0, 5) }}–{{ slot.end_time.slice(0, 5) }}</small></span><button type="button" class="text-button" @click="deleteSlot(slot)">移除</button></article></div>
        <div v-else class="compact-empty">还没有可用时段。没有时段时，后续计划无法自动安排学习块。</div>
      </section>
    </template>

    <section class="panel editor-panel">
      <div class="section-heading"><div><p class="eyebrow">显示</p><h2>界面主题</h2></div></div>
      <div class="segmented" role="group" aria-label="界面主题">
        <button v-for="option in themeOptions" :key="option.value" type="button" class="segmented-item" :class="{ 'is-active': themeMode === option.value }" :aria-pressed="themeMode === option.value" @click="setThemeMode(option.value)">{{ option.label }}</button>
      </div>
      <p class="form-help">选择立即生效，并保存在本机浏览器；“跟随系统”会随操作系统深浅色设置自动切换。主题只影响当前浏览器，不写入工作空间设置。</p>
    </section>
  </main>
</template>
