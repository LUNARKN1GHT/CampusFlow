<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'

import { scheduleApi } from '../api/business'
import { errorMessage } from '../api/client'
import { settingsApi } from '../api/settings'
import type { AvailabilitySlot } from '../api/types'
import ApiFeedback from '../components/ApiFeedback.vue'

const weekdays = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
const commonTimezones = ['Asia/Shanghai', 'Asia/Tokyo', 'Asia/Hong_Kong', 'Asia/Singapore', 'Europe/London', 'America/New_York']
const loading = ref(true)
const saving = ref(false)
const slotSaving = ref(false)
const error = ref('')
const message = ref('')
const slots = ref<AvailabilitySlot[]>([])
const form = reactive({ timezone: 'Asia/Shanghai', daily_capacity_minutes: 240, break_minutes: 15, buffer_minutes: 30 })
const slotForm = reactive({ day_of_week: 0, start_time: '19:00', end_time: '21:00' })
const slotError = ref('')

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [settings, availability] = await Promise.all([settingsApi.get(), scheduleApi.listSlots()])
    Object.assign(form, settings)
    slots.value = availability
  } catch (caught) {
    error.value = errorMessage(caught)
  } finally {
    loading.value = false
  }
}

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
    <ApiFeedback v-if="error && loading" :message="error" :busy="loading" @retry="load" />
    <template v-else>
      <div class="two-column-layout settings-layout">
        <section class="panel">
          <div class="section-heading"><div><p class="eyebrow">时间基准</p><h2>时区与每日容量</h2></div></div>
          <form class="stack-form" @submit.prevent="saveSettings">
            <label>时区<input v-model.trim="form.timezone" list="timezone-options" required /><datalist id="timezone-options"><option v-for="timezone in commonTimezones" :key="timezone" :value="timezone" /></datalist></label>
            <label>每日可学习容量（分钟）<input v-model.number="form.daily_capacity_minutes" type="number" min="0" max="1440" required /></label>
            <div class="form-grid"><label>每段休息（分钟）<input v-model.number="form.break_minutes" type="number" min="0" max="240" required /></label><label>提交缓冲（分钟）<input v-model.number="form.buffer_minutes" type="number" min="0" max="1440" required /></label></div>
            <p class="form-help">默认值：Asia/Shanghai、每日 240 分钟、休息 15 分钟、缓冲 30 分钟。</p>
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
  </main>
</template>
