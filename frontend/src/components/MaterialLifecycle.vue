<script setup lang="ts">
import { ref } from 'vue'

import { errorMessage } from '../api/client'
import { materialsApi } from '../api/library'
import type { Material, MaterialDeleteImpact } from '../api/types'

const props = defineProps<{ material: Material }>()
const emit = defineEmits<{ close: []; archiveChanged: []; deleted: [number] }>()

const archiving = ref(false)
const archiveError = ref('')
const archiveMessage = ref('')

const previewing = ref(false)
const previewError = ref('')
const impact = ref<MaterialDeleteImpact | null>(null)
const keepTasks = ref(true)

const deleting = ref(false)
const deletingError = ref('')
const deleteResult = ref<string | null>(null)

async function toggleArchive() {
  archiving.value = true
  archiveError.value = ''
  archiveMessage.value = ''
  try {
    const updated = await materialsApi[props.material.archived ? 'restore' : 'archive'](props.material.id)
    archiveMessage.value = updated.archived
      ? '已归档：资料退出检索与问答，引用保留并标注为已归档，可随时恢复。'
      : '已恢复：资料重新参与检索与问答，引用重新生效。'
    emit('archiveChanged')
  } catch (caught) {
    archiveError.value = errorMessage(caught)
  } finally {
    archiving.value = false
  }
}

async function previewDelete() {
  previewing.value = true
  previewError.value = ''
  deletingError.value = ''
  deleteResult.value = null
  try {
    impact.value = await materialsApi.deleteImpact(props.material.id)
  } catch (caught) {
    previewError.value = errorMessage(caught)
  } finally {
    previewing.value = false
  }
}

async function confirmDelete() {
  const current = impact.value
  if (!current) return
  deleting.value = true
  deletingError.value = ''
  try {
    // 只提交预览时展示过的范围指纹；内容变化会被后端拒绝，必须重新预览。
    await materialsApi.remove(props.material.id, current.confirm_token, keepTasks.value)
    deleteResult.value = keepTasks.value
      ? '删除完成：原文件与派生内容已移除，个人任务按选择保留并标注来源已删除。'
      : '删除完成：原文件、派生内容与关联个人任务均已移除。'
    emit('deleted', props.material.id)
  } catch (caught) {
    // 删除未完成时不显示成功；资料仍可访问，可重新预览后再删除。
    deletingError.value = errorMessage(caught)
    impact.value = null
  } finally {
    deleting.value = false
  }
}

function staleDelete() {
  impact.value = null
  deleteResult.value = null
  deletingError.value = ''
}
</script>

<template>
  <section class="panel editor-panel lifecycle-panel">
    <div class="section-heading">
      <div><p class="eyebrow">归档与删除</p><h2>{{ material.title }}</h2></div>
      <button type="button" class="text-button" @click="emit('close')">关闭</button>
    </div>

    <div class="lifecycle-layout">
      <div>
        <h3 class="subheading">归档</h3>
        <p class="form-help">归档后资料不参与检索与问答，引用保留并标注来源已归档；恢复后引用重新生效。归档不删除任何片段或正式任务。</p>
        <p v-if="archiveError" class="form-error" role="alert">{{ archiveError }}</p>
        <p v-if="archiveMessage" class="success-message" role="status">{{ archiveMessage }}</p>
        <div class="lifecycle-actions">
          <button type="button" :class="material.archived ? '' : 'button-secondary'" :disabled="archiving" @click="toggleArchive">
            {{ archiving ? '正在处理…' : material.archived ? '恢复已归档资料' : '归档资料' }}
          </button>
        </div>
      </div>

      <div>
        <h3 class="subheading">删除影响预览</h3>
        <p class="form-help">删除会覆盖原文件、解析结果与派生内容。确认只执行下列展示范围；内容变化后需重新预览。</p>
        <p v-if="previewError" class="form-error" role="alert">{{ previewError }}</p>
        <p v-if="deletingError" class="form-error" role="alert">删除未完成：{{ deletingError }}。资料仍可访问，可重新预览后再删除。</p>
        <p v-if="deleteResult" class="success-message" role="status">{{ deleteResult }}</p>
        <button v-if="!impact && !deleteResult" type="button" class="danger-button" :disabled="previewing" @click="previewDelete">
          {{ previewing ? '正在预览…' : '预览删除影响' }}
        </button>

        <div v-if="impact" class="delete-preview">
          <div class="summary-row">
            <div class="summary-chip"><strong>{{ impact.version_count }}</strong><span>个版本将删除</span></div>
            <div class="summary-chip"><strong>{{ impact.chunk_count }}</strong><span>个片段将失效</span></div>
            <div class="summary-chip"><strong>{{ impact.linked_task_ids.length }}</strong><span>个关联任务受影响</span></div>
            <div class="summary-chip"><strong>{{ impact.candidate_count }}</strong><span>个候选事项将删除</span></div>
            <div v-if="impact.linked_fixed_event_ids.length" class="summary-chip"><strong>{{ impact.linked_fixed_event_ids.length }}</strong><span>个固定日程将标记来源失效</span></div>
          </div>
          <p v-if="impact.unfinished_job_count" class="inline-notice" role="status">
            还有 {{ impact.unfinished_job_count }} 个未完成的处理作业会随资料一并清理。
          </p>
          <label class="stack-field">关联的个人任务
            <select v-model="keepTasks">
              <option :value="true">保留任务，标注来源已删除（推荐）</option>
              <option :value="false">随资料一并删除</option>
            </select>
          </label>
          <p class="form-help">{{ impact.backup_note }}</p>

          <div v-if="!deleteResult" class="lifecycle-actions">
            <button type="button" class="text-button" @click="staleDelete">取消</button>
            <button type="button" class="danger-button" :disabled="deleting" @click="confirmDelete">
              {{ deleting ? '正在删除…' : `确认删除以上范围` }}
            </button>
          </div>
          <p v-else class="form-help">该资料已从列表移除。</p>
        </div>
      </div>
    </div>
  </section>
</template>
