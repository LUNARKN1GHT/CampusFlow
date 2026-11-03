<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { apiRequest, errorMessage } from '../api/client'

type Capability = 'text' | 'vision' | 'embedding'
type AIConfig = {
  editable: boolean; text_provider: 'zhipu' | 'deepseek'; text_model: string; vision_model: string
  embedding_model: string; embedding_dimensions: number
  zhipu_configured: boolean; deepseek_configured: boolean; capabilities: Record<Capability, boolean>
}
const config = ref<AIConfig | null>(null)
const error = ref('')
const message = ref('')
const busy = ref(false)
const acknowledgeCost = ref(false)
const checks = reactive<Partial<Record<Capability, string>>>({})
const names: Record<Capability, string> = { text: '文字生成', vision: '图片识别', embedding: '按意思检索资料' }
const form = reactive({ text_provider: 'zhipu' as 'zhipu' | 'deepseek', text_model: '', vision_model: '', zhipu_api_key: '', deepseek_api_key: '', clear_zhipu: false, clear_deepseek: false })

async function load() {
  error.value = ''
  try {
    config.value = await apiRequest<AIConfig>('/settings/ai')
    Object.assign(form, { text_provider: config.value.text_provider, text_model: config.value.text_model, vision_model: config.value.vision_model })
  } catch (caught) { error.value = errorMessage(caught) }
}

async function save() {
  busy.value = true
  error.value = message.value = ''
  try {
    config.value = await apiRequest<AIConfig>('/settings/ai', { method: 'PUT', body: JSON.stringify({
      text_provider: form.text_provider, text_model: form.text_model, vision_model: form.vision_model,
      zhipu_api_key: form.clear_zhipu ? '' : form.zhipu_api_key || null,
      deepseek_api_key: form.clear_deepseek ? '' : form.deepseek_api_key || null,
    }) })
    form.zhipu_api_key = form.deepseek_api_key = ''
    form.clear_zhipu = form.clear_deepseek = false
    for (const key of Object.keys(names) as Capability[]) delete checks[key]
    message.value = '已安全保存，新请求无需重启即可使用。正在执行的请求可能仍使用之前的配置；已失败资料需另外重试。'
  } catch (caught) { error.value = errorMessage(caught) }
  finally { busy.value = false }
}

async function check(capability: Capability) {
  busy.value = true
  error.value = ''
  checks[capability] = '正在检查已保存的配置…'
  try {
    const result = await apiRequest<{ ok: boolean; message: string }>('/settings/ai/check', {
      method: 'POST', body: JSON.stringify({ capability, acknowledge_cost: acknowledgeCost.value }),
    }, 130000)
    checks[capability] = `${result.ok ? '通过' : '未通过'}：${result.message}`
  } catch (caught) { checks[capability] = errorMessage(caught) }
  finally { busy.value = false }
}
onMounted(load)
</script>

<template>
  <section id="ai-settings" class="panel editor-panel" aria-label="AI 服务设置">
    <div class="section-heading"><h2>AI 服务设置</h2><button type="button" class="text-button" :disabled="busy" @click="load">重新读取 AI 配置</button></div>
    <p class="form-help">文字生成负责理解与回答；图片识别负责读取截图；检索负责把原文转换成按意思查找的数据。没有配置也能使用手动任务功能。</p>
    <p v-if="error" class="form-error" role="alert">{{ error }}</p>
    <template v-if="config">
      <ul class="plain-list"><li v-for="(name, capability) in names" :key="capability">{{ name }}：{{ config.capabilities[capability] ? '已配置（尚需检查连接）' : '未配置' }}</li></ul>
      <p v-if="!config.editable" class="form-help">当前是网页版，模型设置由服务管理员维护。</p>
      <form v-else class="stack-form" @submit.prevent="save">
        <fieldset :disabled="busy">
          <div class="form-grid">
            <label>文字服务<select v-model="form.text_provider"><option value="zhipu">智谱</option><option value="deepseek">DeepSeek</option></select></label>
            <label>文字模型型号<input v-model.trim="form.text_model" required maxlength="100" /></label>
            <label>图片模型型号（智谱）<input v-model.trim="form.vision_model" required maxlength="100" /></label>
          </div>
          <p class="form-help">切换服务时请填写该服务支持的型号。检索沿用 {{ config.embedding_model }}（{{ config.embedding_dimensions }} 维），避免混用已有资料索引。</p>
          <label>智谱密钥（图片、检索及智谱文字共用）<input v-model="form.zhipu_api_key" type="password" autocomplete="new-password" maxlength="512" :disabled="form.clear_zhipu" placeholder="留空保留已有密钥" /></label>
          <label><input v-model="form.clear_zhipu" type="checkbox" />清除已保存的智谱密钥</label>
          <label>DeepSeek 密钥（仅文字）<input v-model="form.deepseek_api_key" type="password" autocomplete="new-password" maxlength="512" :disabled="form.clear_deepseek" placeholder="留空保留已有密钥" /></label>
          <label><input v-model="form.clear_deepseek" type="checkbox" />清除已保存的 DeepSeek 密钥</label>
          <p class="form-help">密钥由本机凭据存储保存，不会在页面中回显。保存设置本身不会调用模型。</p>
          <button type="submit">保存 AI 设置</button>
        </fieldset>
        <p v-if="message" class="success-message" role="status">{{ message }}</p>
      </form>
      <template v-if="config.editable">
        <p class="form-help">检查使用已保存的配置，向所选服务发送一份合成文字或图片，可能产生模型调用费用。每项单独检查，结果互不代替。</p>
        <label><input v-model="acknowledgeCost" type="checkbox" :disabled="busy" />我同意用合成样本检查，了解可能产生费用</label>
        <div v-for="(name, capability) in names" :key="capability">
          <button type="button" class="button-secondary" :disabled="busy || !acknowledgeCost || !config.capabilities[capability]" @click="check(capability)">检查{{ name }}</button>
          <p v-if="checks[capability]" role="status">{{ checks[capability] }}</p>
        </div>
      </template>
    </template>
  </section>
</template>
