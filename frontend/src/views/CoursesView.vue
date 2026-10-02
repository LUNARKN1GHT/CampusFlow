<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import { semestersApi } from '../api/business'
import { errorMessage } from '../api/client'
import type { Semester } from '../api/types'
import ApiFeedback from '../components/ApiFeedback.vue'

const semesters = ref<Semester[]>([])
const loading = ref(true)
const saving = ref(false)
const error = ref('')
const formError = ref('')
const showHistory = ref(false)
const selectedId = ref<number | null>(null)
const form = reactive({ name: '', start_date: '', end_date: '' })

const visibleSemesters = computed(() => showHistory.value
  ? semesters.value
  : semesters.value.filter((semester) => !semester.archived))
const selectedSemester = computed(() => semesters.value.find((semester) => semester.id === selectedId.value) ?? null)

async function load() {
  loading.value = true
  error.value = ''
  try {
    semesters.value = await semestersApi.list(true)
    const available = semesters.value.filter((semester) => !semester.archived)
    if (!semesters.value.some((semester) => semester.id === selectedId.value && !semester.archived)) {
      selectedId.value = available[0]?.id ?? null
    }
  } catch (caught) {
    error.value = errorMessage(caught)
  } finally {
    loading.value = false
  }
}

async function createSemester() {
  formError.value = ''
  if (form.end_date < form.start_date) {
    formError.value = '结束日期不能早于开始日期。'
    return
  }
  saving.value = true
  try {
    const created = await semestersApi.create(form)
    semesters.value.push(created)
    selectedId.value = created.id
    Object.assign(form, { name: '', start_date: '', end_date: '' })
  } catch (caught) {
    formError.value = errorMessage(caught)
  } finally {
    saving.value = false
  }
}

async function toggleArchived(semester: Semester) {
  formError.value = ''
  try {
    const updated = await semestersApi.setArchived(semester.id, !semester.archived)
    const index = semesters.value.findIndex((item) => item.id === semester.id)
    semesters.value[index] = updated
    if (updated.archived && selectedId.value === updated.id) {
      selectedId.value = semesters.value.find((item) => !item.archived)?.id ?? null
    }
  } catch (caught) {
    formError.value = errorMessage(caught)
  }
}

onMounted(load)
</script>

<template>
  <main class="page-content">
    <div class="page-heading page-heading-row">
      <div>
        <p class="eyebrow">学期与课程</p>
        <h1>课程</h1>
        <p class="intro">先确定正在查看的学期，再管理其中的课程与教学班。</p>
      </div>
      <label class="switch-field"><input v-model="showHistory" type="checkbox" /> 显示历史学期</label>
    </div>

    <ApiFeedback v-if="error" :message="error" :busy="loading" @retry="load" />
    <div v-else class="two-column-layout">
      <section class="panel">
        <div class="section-heading">
          <div><p class="eyebrow">学期列表</p><h2>选择当前查看范围</h2></div>
          <span class="count-badge">{{ visibleSemesters.length }} 个</span>
        </div>
        <p v-if="loading" class="muted">正在加载学期…</p>
        <div v-else-if="visibleSemesters.length" class="semester-list">
          <article
            v-for="semester in visibleSemesters"
            :key="semester.id"
            :class="['semester-card', { selected: selectedId === semester.id, archived: semester.archived }]"
          >
            <button class="semester-select" type="button" @click="selectedId = semester.id">
              <strong>{{ semester.name }}</strong>
              <span>{{ semester.start_date }} — {{ semester.end_date }}</span>
            </button>
            <button class="text-button" type="button" @click="toggleArchived(semester)">
              {{ semester.archived ? '恢复' : '归档' }}
            </button>
          </article>
        </div>
        <div v-else class="compact-empty">{{ showHistory ? '还没有任何学期。' : '没有进行中的学期，可新建或查看历史学期。' }}</div>
      </section>

      <section class="panel">
        <div class="section-heading"><div><p class="eyebrow">新建</p><h2>添加学期</h2></div></div>
        <form class="stack-form" @submit.prevent="createSemester">
          <label>学期名称<input v-model.trim="form.name" required maxlength="100" placeholder="例如：2026 秋季学期" /></label>
          <div class="form-grid">
            <label>开始日期<input v-model="form.start_date" type="date" required /></label>
            <label>结束日期<input v-model="form.end_date" type="date" required /></label>
          </div>
          <p v-if="formError" class="form-error" role="alert">{{ formError }}</p>
          <button type="submit" :disabled="saving">{{ saving ? '正在保存…' : '保存学期' }}</button>
        </form>
      </section>
    </div>

    <section class="panel courses-placeholder">
      <div class="section-heading"><div><p class="eyebrow">所选学期</p><h2>{{ selectedSemester?.name ?? '请先选择学期' }}</h2></div></div>
      <div class="compact-empty">课程列表将在下一项中接入真实 API。</div>
    </section>
  </main>
</template>
