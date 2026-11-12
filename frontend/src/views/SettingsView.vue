<script setup lang="ts">
/*
 * 设置页：维护工作空间时区、每日／每周学习容量、休息与提交缓冲，以及每周可用时段。
 * 偏好只影响后续生成或重排计划时的时间解释与容量，不改写已存日程、任务 DDL 或既有计划。
 * AI 服务面板只在桌面版可编辑：密钥写本机凭据存储，页面只显示是否已保存，绝不回显密钥。
 * 保存后服务端会按新的工作空间时区重算提醒窗口；界面主题保存在本机浏览器，不写入工作空间。
 */
import { onMounted, reactive, ref } from 'vue'
import { RouterLink } from 'vue-router'

import { scheduleApi } from '../api/business'
import { errorMessage } from '../api/client'
import { aiSettingsApi, settingsApi } from '../api/settings'
import type { AiSettingsStatus, AvailabilitySlot, TextProvider } from '../api/types'
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
const {
  options: themeOptions,
  paletteOptions,
  mode: themeMode,
  palette,
  setThemeMode,
  setColorPalette,
} = useTheme()

// AI 服务：后端只返回可用性；密钥输入框永远是空白，提交后立即清空本地副本。
const providerLabels: Record<TextProvider, string> = { zhipu: '智谱 GLM（ZHIPU）', deepseek: 'DeepSeek' }
const secretBackendLabels: Record<string, string> = {
  environment: '环境变量',
  'macos-keychain': 'macOS 钥匙串',
  'windows-dpapi': 'Windows DPAPI',
  file: '数据目录内 0600 凭据文件',
  memory: '内存（仅测试）',
  unavailable: '不可用',
}
const ai = ref<AiSettingsStatus | null>(null)
const aiLoading = ref(true)
const aiSaving = ref(false)
const aiLoadError = ref('')
const aiError = ref('')
const aiMessage = ref('')
const aiForm = reactive({
  text_provider: 'zhipu' as TextProvider,
  zhipu_api_key: '',
  deepseek_api_key: '',
  clear_zhipu_api_key: false,
  clear_deepseek_api_key: false,
})

function applyAiStatus(status: AiSettingsStatus) {
  ai.value = status
  aiForm.text_provider = status.text_provider
}

// AI 配置读取失败只影响本面板，不阻塞时区与可用时间的编辑。
async function loadAiSettings() {
  aiLoading.value = true
  aiLoadError.value = ''
  try {
    applyAiStatus(await aiSettingsApi.get())
  } catch (caught) {
    aiLoadError.value = errorMessage(caught)
  } finally {
    aiLoading.value = false
  }
}

// 只提交用户实际填写的字段：留空的密钥表示"不修改"，清除必须显式勾选。
// 服务端仍会校验最终供应方是否有对应密钥，失败时不保存任何一项。
async function saveAiSettings() {
  aiError.value = ''
  aiMessage.value = ''
  if (aiForm.zhipu_api_key && aiForm.clear_zhipu_api_key) {
    aiError.value = '不能同时填写并清除 ZHIPU 密钥，请只保留一种操作。'
    return
  }
  if (aiForm.deepseek_api_key && aiForm.clear_deepseek_api_key) {
    aiError.value = '不能同时填写并清除 DeepSeek 密钥，请只保留一种操作。'
    return
  }
  aiSaving.value = true
  try {
    const updated = await aiSettingsApi.update({
      text_provider: aiForm.text_provider,
      ...(aiForm.zhipu_api_key ? { zhipu_api_key: aiForm.zhipu_api_key } : {}),
      ...(aiForm.deepseek_api_key ? { deepseek_api_key: aiForm.deepseek_api_key } : {}),
      ...(aiForm.clear_zhipu_api_key ? { clear_zhipu_api_key: true } : {}),
      ...(aiForm.clear_deepseek_api_key ? { clear_deepseek_api_key: true } : {}),
    })
    applyAiStatus(updated)
    aiForm.zhipu_api_key = ''
    aiForm.deepseek_api_key = ''
    aiForm.clear_zhipu_api_key = false
    aiForm.clear_deepseek_api_key = false
    aiMessage.value = 'AI 配置已保存，文字问答与检索立即使用新密钥；后台资料处理需重启应用生效。'
  } catch (caught) {
    aiError.value = errorMessage(caught)
  } finally {
    aiSaving.value = false
  }
}

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

onMounted(() => {
  load()
  loadAiSettings()
})
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
      <div class="section-heading"><div><p class="eyebrow">模型配置</p><h2>AI 服务</h2></div><span v-if="ai" class="count-badge">{{ ai.editable ? '可在应用内保存' : '由环境变量配置' }}</span></div>
      <p v-if="aiLoadError" class="form-error" role="alert">{{ aiLoadError }} <button type="button" class="text-button" @click="loadAiSettings">重新读取</button></p>
      <p v-else-if="aiLoading" class="form-help" role="status">正在读取 AI 配置…</p>
      <template v-else-if="ai">
        <div class="availability-grid">
          <article><span><strong>文字生成</strong><small>当前供应方：{{ ai.text_provider === 'zhipu' ? 'ZHIPU' : 'DeepSeek' }}</small></span><span class="status-pill ai-status-pill" :data-status="ai.text_available ? 'done' : 'failed'">{{ ai.text_available ? '可用' : '缺少密钥' }}</span></article>
          <article><span><strong>图片识别</strong><small>使用 ZHIPU 密钥</small></span><span class="status-pill ai-status-pill" :data-status="ai.vision_available ? 'done' : 'failed'">{{ ai.vision_available ? '可用' : '缺少密钥' }}</span></article>
          <article><span><strong>向量检索</strong><small>使用 ZHIPU 密钥</small></span><span class="status-pill ai-status-pill" :data-status="ai.embedding_available ? 'done' : 'failed'">{{ ai.embedding_available ? '可用' : '缺少密钥' }}</span></article>
        </div>
        <p class="form-help">安全存储：{{ secretBackendLabels[ai.secret_backend] ?? ai.secret_backend }}。页面只显示密钥是否已保存，不显示任何密钥内容或片段。</p>
        <p v-for="warning in ai.warnings" :key="warning" class="inline-notice">{{ warning }}</p>
        <p v-if="ai.restart_required" class="inline-notice">已保存的 AI 配置需要重启应用，后台资料处理（解析与索引）才会使用新密钥；前台问答与检索已使用新配置。</p>
        <form v-if="ai.editable" class="stack-form" @submit.prevent="saveAiSettings">
          <label>文字生成供应方<select v-model="aiForm.text_provider"><option value="zhipu">{{ providerLabels.zhipu }}</option><option value="deepseek">{{ providerLabels.deepseek }}</option></select></label>
          <div class="form-grid">
            <label>ZHIPU API 密钥（{{ ai.zhipu_configured ? '已保存，留空不修改' : '尚未保存' }}）<input v-model.trim="aiForm.zhipu_api_key" type="password" autocomplete="new-password" placeholder="留空表示不修改" /></label>
            <label>DeepSeek API 密钥（{{ ai.deepseek_configured ? '已保存，留空不修改' : '尚未保存' }}）<input v-model.trim="aiForm.deepseek_api_key" type="password" autocomplete="new-password" placeholder="留空表示不修改" /></label>
          </div>
          <div class="form-grid">
            <label class="switch-field"><input v-model="aiForm.clear_zhipu_api_key" type="checkbox" />清除已保存的 ZHIPU 密钥</label>
            <label class="switch-field"><input v-model="aiForm.clear_deepseek_api_key" type="checkbox" />清除已保存的 DeepSeek 密钥</label>
          </div>
          <p class="form-help">ZHIPU 密钥同时用于文字生成、图片识别和向量检索；DeepSeek 密钥只用于文字生成。密钥写入本机凭据存储，不进入页面、日志或导出文件。保存后文字问答立即生效，后台资料处理需重启应用生效。</p>
          <p v-if="aiError" class="form-error" role="alert">{{ aiError }}</p><p v-if="aiMessage" class="success-message" role="status">{{ aiMessage }}</p>
          <button type="submit" :disabled="aiSaving">{{ aiSaving ? '正在保存…' : '保存 AI 配置' }}</button>
        </form>
        <p v-else class="form-help">服务器／开发环境由环境变量配置模型密钥：ZHIPU_API_KEY、DEEPSEEK_API_KEY，文字供应方由 CAMPUSFLOW_TEXT_PROVIDER 决定（默认 zhipu）。桌面版可在本页直接填写密钥并保存。</p>
      </template>
    </section>

    <section class="panel editor-panel">
      <div class="section-heading"><div><p class="eyebrow">显示</p><h2>界面主题</h2></div></div>
      <div class="theme-stack">
        <div class="segmented" role="group" aria-label="界面主题">
          <button v-for="option in themeOptions" :key="option.value" type="button" class="segmented-item" :class="{ 'is-active': themeMode === option.value }" :aria-pressed="themeMode === option.value" @click="setThemeMode(option.value)">{{ option.label }}</button>
        </div>
        <div class="segmented" role="group" aria-label="主题色系">
          <button v-for="option in paletteOptions" :key="option.value" type="button" class="segmented-item" :class="{ 'is-active': palette === option.value }" :aria-pressed="palette === option.value" @click="setColorPalette(option.value)">{{ option.label }}</button>
        </div>
      </div>
      <p class="form-help">深浅模式与色系互相独立：绿色为默认，蓝色和白色会替换侧栏、按钮和选中态的主色。选择立即生效，并保存在本机浏览器；“跟随系统”会随操作系统深浅色设置自动切换。主题只影响当前浏览器，不写入工作空间设置。</p>
    </section>

    <section class="panel editor-panel">
      <div class="section-heading"><div><p class="eyebrow">低频功能</p><h2>工具箱</h2></div></div>
      <p class="form-help">课程路径等低频能力不在左侧主导航展示，集中放在工具箱里按需进入；入口位置调整不影响已保存的培养方案、课程关系与计划。</p>
      <RouterLink class="toolbox-link" to="/tools">进入工具箱</RouterLink>
    </section>
  </main>
</template>

<style scoped>
.toolbox-link { display: inline-flex; margin-top: 14px; padding: 9px 14px; border: 1px solid var(--brand-border); border-radius: 10px; color: var(--brand); background: var(--brand-soft); font-size: 13px; font-weight: 800; text-decoration: none; }
.toolbox-link:hover { border-color: var(--brand); }
</style>
