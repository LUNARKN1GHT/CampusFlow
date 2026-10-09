<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'

import { coursesApi, semestersApi } from '../api/business'
import { errorMessage } from '../api/client'
import type { Course, CourseInput, Semester } from '../api/types'
import ApiFeedback from '../components/ApiFeedback.vue'

const semesters = ref<Semester[]>([])
const loading = ref(true)
const saving = ref(false)
const error = ref('')
const formError = ref('')
const showHistory = ref(false)
const selectedId = ref<number | null>(null)
const form = reactive({ name: '', start_date: '', end_date: '' })
const courses = ref<Course[]>([])
const coursesLoading = ref(false)
const courseSaving = ref(false)
const courseError = ref('')
const editingCourse = ref<Course | null>(null)
const editorOpen = ref(false)
const courseForm = reactive({ name: '', code: '', teacher: '', class_name: '' })

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

async function loadCourses() {
  courses.value = []
  courseError.value = ''
  if (selectedId.value === null) return
  coursesLoading.value = true
  try {
    courses.value = await coursesApi.list(selectedId.value)
  } catch (caught) {
    courseError.value = errorMessage(caught)
  } finally {
    coursesLoading.value = false
  }
}

function editCourse(course?: Course) {
  editorOpen.value = true
  editingCourse.value = course ?? null
  Object.assign(courseForm, {
    name: course?.name ?? '',
    code: course?.code ?? '',
    teacher: course?.teacher ?? '',
    class_name: course?.class_name ?? '',
  })
  courseError.value = ''
}

async function saveCourse() {
  if (selectedId.value === null) return
  courseSaving.value = true
  courseError.value = ''
  const values = {
    name: courseForm.name,
    code: courseForm.code || null,
    teacher: courseForm.teacher || null,
    class_name: courseForm.class_name || null,
  }
  try {
    if (editingCourse.value) {
      const patch: Partial<CourseInput> = {}
      for (const key of Object.keys(values) as Array<keyof typeof values>) {
        if (values[key] !== editingCourse.value[key]) Object.assign(patch, { [key]: values[key] })
      }
      if (Object.keys(patch).length) {
        const updated = await coursesApi.update(editingCourse.value.id, patch)
        courses.value[courses.value.findIndex((course) => course.id === updated.id)] = updated
      }
    } else {
      courses.value.push(await coursesApi.create({ semester_id: selectedId.value, ...values }))
    }
    editorOpen.value = false
    editingCourse.value = null
    Object.assign(courseForm, { name: '', code: '', teacher: '', class_name: '' })
  } catch (caught) {
    courseError.value = errorMessage(caught)
  } finally {
    courseSaving.value = false
  }
}

watch(selectedId, loadCourses)
watch(showHistory, (visible) => {
  if (!visible && selectedSemester.value?.archived) {
    selectedId.value = semesters.value.find((semester) => !semester.archived)?.id ?? null
  }
})

onMounted(load)
</script>

<template>
  <main class="page-content">
    <div class="page-heading page-heading-row">
      <div>
        <p class="eyebrow">学期与课程</p>
        <h1>课程</h1>
        <p class="intro">先确定正在查看的学期，再管理其中的课程与教学班。</p>
        <p class="form-help">归档只隐藏进行中学期入口，不删除课程、任务或日程；勾选“显示历史学期”可查看和恢复。</p>
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
      <p v-if="selectedSemester?.archived" class="form-help">正在查看已归档学期：记录仍保留并可维护，恢复不会重置任务进度或截止日期。</p>
      <div class="section-heading">
        <div><p class="eyebrow">所选学期</p><h2>{{ selectedSemester?.name ?? '请先选择学期' }}</h2></div>
        <button v-if="selectedSemester" type="button" @click="editCourse()">添加课程</button>
      </div>
      <ApiFeedback v-if="courseError && !editingCourse" :message="courseError" :busy="coursesLoading" @retry="loadCourses" />
      <p v-else-if="coursesLoading" class="muted">正在加载课程…</p>
      <div v-else-if="courses.length" class="course-grid">
        <article v-for="course in courses" :key="course.id" class="course-card">
          <div class="course-code">{{ course.code || '未填写代码' }}</div>
          <h3>{{ course.name }}</h3>
          <dl>
            <div><dt>教学班</dt><dd>{{ course.class_name || '未填写' }}</dd></div>
            <div><dt>教师</dt><dd>{{ course.teacher || '未填写' }}</dd></div>
          </dl>
          <button type="button" class="button-secondary" @click="editCourse(course)">编辑课程</button>
        </article>
      </div>
      <div v-else class="compact-empty">{{ selectedSemester ? '这个学期还没有课程。' : '选择学期后查看课程。' }}</div>
    </section>

    <section v-if="selectedSemester && (editorOpen || !courses.length)" class="panel editor-panel">
      <div class="section-heading">
        <div><p class="eyebrow">{{ editingCourse ? '编辑' : '新建' }}</p><h2>{{ editingCourse?.name || '添加课程' }}</h2></div>
        <button v-if="editorOpen" type="button" class="text-button" @click="editorOpen = false">取消</button>
      </div>
      <form class="stack-form" @submit.prevent="saveCourse">
        <div class="form-grid">
          <label>课程名称<input v-model.trim="courseForm.name" required maxlength="200" /></label>
          <label>课程代码<input v-model.trim="courseForm.code" maxlength="50" placeholder="例如：CS301" /></label>
          <label>教学班<input v-model.trim="courseForm.class_name" maxlength="100" placeholder="例如：1 班" /></label>
          <label>教师<input v-model.trim="courseForm.teacher" maxlength="100" /></label>
        </div>
        <p class="form-help">同名课程会通过课程代码和教学班区分；留空的可选字段会明确保存为空。</p>
        <p v-if="courseError" class="form-error" role="alert">{{ courseError }}</p>
        <button type="submit" :disabled="courseSaving">{{ courseSaving ? '正在保存…' : '保存课程' }}</button>
      </form>
    </section>
  </main>
</template>
