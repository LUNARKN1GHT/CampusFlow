<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'

import { errorMessage } from '../api/client'
import { materialsApi } from '../api/library'
import type { Material, MaterialVersion, SourceChunk } from '../api/types'

const props = defineProps<{
  material: Material
  scope: string
  // 候选引用属于某个具体版本与片段；不传时仍默认打开最新版本。
  initialVersionNo?: number | null
  initialChunkId?: number | null
}>()
const emit = defineEmits<{ close: [] }>()

const versions = ref<MaterialVersion[]>([])
const versionNo = ref<number | null>(null)
const chunks = ref<SourceChunk[]>([])
const selected = ref<SourceChunk | null>(null)
const loading = ref(true)
const error = ref('')
const sourceError = ref('')
const loadingSource = ref(false)
const objectUrl = ref<string | null>(null)
const originalText = ref('')
const textSegments = computed(() => {
  const text = originalText.value
  let cursor = 0
  for (const chunk of chunks.value) {
    const index = text.indexOf(chunk.text, cursor)
    if (index < 0) continue
    if (chunk.id === selected.value?.id) return {
      before: text.slice(0, index), match: chunk.text, after: text.slice(index + chunk.text.length),
    }
    cursor = index + chunk.text.length
  }
  return { before: text, match: '', after: '' }
})
let disposed = false

const sourceTypeLabels: Record<string, string> = {
  pdf: 'PDF 页', image: '截图区域', text: '文本段落', word: 'Word', web: '网页',
}

const selectedLocator = computed(() => {
  const chunk = selected.value
  if (!chunk) return '未选择片段'
  if (chunk.locator_type === 'page' && chunk.page) {
    const coords = chunk.bbox ? `；区域坐标 x ${chunk.bbox[0]}–${chunk.bbox[2]}、y ${chunk.bbox[1]}–${chunk.bbox[3]}（归一化）` : ''
    return `第 ${chunk.page} 页${coords}`
  }
  if (chunk.locator_type === 'paragraph' && chunk.paragraph) return `第 ${chunk.paragraph} 段`
  if (chunk.locator_type === 'region' && chunk.bbox) {
    return `区域 x ${chunk.bbox[0]}–${chunk.bbox[2]}、y ${chunk.bbox[1]}–${chunk.bbox[3]}（归一化）`
  }
  return chunk.locator_value
})

const citationLabel = computed(() => {
  const chunk = selected.value
  if (!chunk || versionNo.value === null) return '—'
  return `资料 #${props.material.id} · 版本 #${versionNo.value} · 片段 #${chunk.id}`
})

const bboxStyle = computed(() => {
  const bbox = selected.value?.bbox
  if (!bbox) return null
  const [x0, y0, x1, y1] = bbox
  return {
    left: `${x0 * 100}%`,
    top: `${y0 * 100}%`,
    width: `${Math.max(x1 - x0, 0) * 100}%`,
    height: `${Math.max(y1 - y0, 0) * 100}%`,
  }
})

function releaseObjectUrl() {
  if (objectUrl.value) {
    URL.revokeObjectURL(objectUrl.value)
    objectUrl.value = null
  }
}

async function loadSource(): Promise<void> {
  releaseObjectUrl()
  sourceError.value = ''
  originalText.value = ''
  if (!['pdf', 'image', 'text'].includes(props.material.source_type)) return
  if (versionNo.value === null) return
  loadingSource.value = true
  try {
    const blob = await materialsApi.fileBlob(props.material.id, versionNo.value)
    if (disposed) return
    if (props.material.source_type === 'text') originalText.value = await blob.text()
    else objectUrl.value = URL.createObjectURL(blob)
  } catch (caught) {
    // 原文件不可用时只显示失效原因，不渲染原文，也不声称引用仍可验证。
    sourceError.value = errorMessage(caught)
  } finally {
    loadingSource.value = false
  }
}

async function loadAll(): Promise<void> {
  loading.value = true
  error.value = ''
  releaseObjectUrl()
  try {
    versions.value = await materialsApi.versions(props.material.id)
    const latest = versions.value.reduce<number | null>(
      (max, item) => (max === null || item.version_no > max ? item.version_no : max),
      null,
    )
    // 引用所属版本与最新版本不一致时按引用打开，否则会把旧版本的摘录定位到高亮不到它的新版本上。
    const pinned = props.initialVersionNo ?? undefined
    versionNo.value =
      pinned !== undefined && versions.value.some((item) => item.version_no === pinned)
        ? pinned
        : latest
    chunks.value = versionNo.value === null
      ? []
      : await materialsApi.chunks(props.material.id, versionNo.value)
    selected.value =
      chunks.value.find((chunk) => chunk.id === props.initialChunkId) ?? chunks.value[0] ?? null
    loading.value = false
    await loadSource()
  } catch (caught) {
    error.value = errorMessage(caught)
    loading.value = false
  }
}

// 面板切换到另一份资料时重新加载，避免沿用上一份的版本与片段。
watch(() => props.material.id, loadAll)

onBeforeUnmount(() => { disposed = true; releaseObjectUrl() })

void loadAll()
</script>

<template>
  <section class="panel editor-panel source-panel">
    <div class="section-heading">
      <div><p class="eyebrow">原文定位</p><h2>{{ material.title }}</h2></div>
      <button type="button" class="text-button" @click="emit('close')">关闭</button>
    </div>

    <div v-if="error" class="status-banner failed" role="alert">
      <div><strong>无法读取原文信息</strong><p>{{ error }}</p></div>
    </div>
    <template v-else>
      <div v-if="material.archived" class="inline-notice" role="status">来源已归档：引用保留并标注为已归档，恢复资料后引用会重新生效。</div>

      <div v-if="loading" class="muted">正在加载原文与片段…</div>
      <div v-else-if="!chunks.length" class="compact-empty">这份资料还没有可定位的片段。</div>
      <div v-else class="source-layout">
        <div class="source-fragments">
          <h3 class="subheading">来源片段</h3>
          <ol class="chunk-list">
            <li v-for="chunk in chunks" :key="chunk.id">
              <button
                type="button"
                class="chunk-item"
                :class="{ 'is-active': selected?.id === chunk.id }"
                :aria-pressed="selected?.id === chunk.id"
                @click="selected = chunk"
              >
                <span class="chunk-locator">{{ chunk.locator_type === 'paragraph' ? `第 ${chunk.paragraph} 段` : chunk.locator_type === 'region' ? '截图区域' : `第 ${chunk.page} 页` }}</span>
                <span class="chunk-text">{{ chunk.text }}</span>
              </button>
            </li>
          </ol>
        </div>

        <div class="source-viewer" :data-source-type="material.source_type">
          <div v-if="sourceError" class="status-banner failed" role="alert">
            <div>
              <strong>原文件暂不可读取，引用目前无法验证</strong>
              <p>{{ sourceError }}</p>
              <p>请检查连接或来源是否仍可用，再重新打开原文。</p>
            </div>
          </div>
          <template v-else>
            <p v-if="loadingSource" class="muted">正在读取原文件…</p>

            <div v-else-if="material.source_type === 'text'" class="text-source">
              <pre style="white-space: pre-wrap; font: inherit">{{ textSegments.before }}<mark v-if="textSegments.match" class="highlight">{{ textSegments.match }}</mark>{{ textSegments.after }}</pre>
            </div>

            <div v-else-if="material.source_type === 'image'" class="image-stage" :class="{ 'is-loading': loadingSource }">
              <img v-if="objectUrl" :src="objectUrl" alt="资料原图" />
              <div v-if="bboxStyle" class="bbox-highlight" :style="bboxStyle" aria-hidden="true"></div>
            </div>

            <div v-else-if="material.source_type === 'pdf'" class="pdf-stage">
              <iframe
                v-if="objectUrl"
                :src="`${objectUrl}#page=${selected?.page ?? 1}&view=FitH`"
                :title="`${material.title} 第 ${selected?.page ?? 1} 页`"
              ></iframe>
              <p class="form-help">浏览器内置阅读器定位到第 {{ selected?.page ?? 1 }} 页；页码来自解析结果，坐标若有则以侧栏为准。</p>
            </div>

            <div v-else class="compact-empty">该来源类型暂不支持原文定位。</div>
          </template>

          <aside class="locator-detail" aria-label="引用详情">
            <div class="status-banner" :class="sourceError ? 'failed' : 'success'">
              <div>
                <strong>{{ sourceError ? '目前无法验证' : loadingSource ? '正在核对原文件' : '引用位置可验证' }}</strong>
                <p>{{ sourceError ? '原文件未读取，仅显示历史定位信息。' : '定位信息对应所选片段。' }}</p>
              </div>
            </div>
            <div class="locator-row"><span>引用标识</span><strong>{{ citationLabel }}</strong></div>
            <div class="locator-row"><span>来源类型</span><strong>{{ sourceTypeLabels[material.source_type] ?? material.source_type }}</strong></div>
            <div class="locator-row"><span>位置</span><strong>{{ selectedLocator }}</strong></div>
            <div class="locator-row"><span>适用范围</span><strong>{{ scope }}</strong></div>
            <div v-if="selected?.confidence !== null && selected?.confidence !== undefined" class="locator-row"><span>识别置信度</span><strong>{{ selected.confidence }}</strong></div>
            <div v-if="selected?.table_group" class="locator-row"><span>跨页表格</span><strong>{{ selected.table_group }}</strong></div>
          </aside>
        </div>
      </div>
    </template>
  </section>
</template>
