<script setup lang="ts">
/*
 * 智能问答页：按学期／课程／教学班确定检索范围，创建或续用会话提问，并分开展示回答、引用与覆盖提示。
 * 流程：选择范围 → 提问（无会话时新建，范围变更时先显式切换）→ 每轮保存范围与引用快照 → 打开引用定位资料库原文。
 * 约束：范围由服务端再次校验；旧轮次引用按当前来源状态重新核对，失效引用不可点击，也不计为可验证依据。
 */
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import ApiFeedback from '../components/ApiFeedback.vue'
import { coursesApi, semestersApi } from '../api/business'
import { errorMessage } from '../api/client'
import { qaApi } from '../api/qa'
import type {
  QaCitation,
  QaConversation,
  QaCoverage,
  QaMode,
  QaScope,
  QaTurn,
} from '../api/qa'
import type { Course, Semester } from '../api/types'

const router = useRouter()
const semesters = ref<Semester[]>([])
const courses = ref<Course[]>([])
const conversations = ref<QaConversation[]>([])
const turns = ref<QaTurn[]>([])
const coverage = ref<QaCoverage[]>([])
const pendingConflictCount = ref(0)
const conversation = ref<QaConversation | null>(null)
const selectedSemester = ref<number | ''>('')
const selectedCourse = ref<number | ''>('')
const selectedClass = ref('')
const mode = ref<QaMode>('hybrid')
const question = ref('')
const loading = ref(false)
const error = ref('')
const loaded = ref(false)

const filteredCourses = computed(() =>
  selectedSemester.value
    ? courses.value.filter((course) => course.semester_id === Number(selectedSemester.value))
    : courses.value,
)
const classOptions = computed(() =>
  [...new Set(filteredCourses.value.map((course) => course.class_name).filter((name): name is string => Boolean(name)))],
)
const currentScope = computed<QaScope>(() => ({
  semester_id: selectedSemester.value ? Number(selectedSemester.value) : null,
  course_id: selectedCourse.value ? Number(selectedCourse.value) : null,
  class_name: selectedClass.value || null,
}))
const invalidCitationCount = computed(() =>
  turns.value.reduce(
    (total, turn) => total + turn.citations.filter((citation) => !citation.valid).length,
    0,
  ),
)
// 会话创建时绑定范围；筛选被改动后必须显式切换范围，避免追问把不同学期／课程的依据混在一起。
const scopeChanged = computed(() => {
  if (!conversation.value) return false
  return (
    conversation.value.semester_id !== currentScope.value.semester_id ||
    conversation.value.course_id !== currentScope.value.course_id ||
    conversation.value.class_name !== currentScope.value.class_name
  )
})

function applyScope(scope: QaScope) {
  selectedSemester.value = scope.semester_id ?? ''
  selectedCourse.value = scope.course_id ?? ''
  selectedClass.value = scope.class_name ?? ''
}

async function loadOptions() {
  try {
    ;[semesters.value, courses.value, conversations.value] = await Promise.all([
      semestersApi.list(true),
      coursesApi.list(),
      qaApi.listConversations(),
    ])
  } catch (cause) {
    error.value = errorMessage(cause)
  }
}

// 读取会话详情时后端会重新核对每轮引用；已失效引用仍随轮次返回，只是不能再跳转到原文。
async function loadConversation(id: number) {
  loading.value = true
  error.value = ''
  try {
    const detail = await qaApi.getConversation(id)
    conversation.value = detail.conversation
    turns.value = detail.turns
    coverage.value = detail.coverage
    pendingConflictCount.value = detail.pending_conflict_count
    mode.value = detail.conversation.mode
    applyScope(detail.conversation)
    loaded.value = true
  } catch (cause) {
    error.value = errorMessage(cause)
  } finally {
    loading.value = false
  }
}

async function selectConversation(event: Event) {
  const value = Number((event.target as HTMLSelectElement).value)
  if (!value) {
    conversation.value = null
    turns.value = []
    coverage.value = []
    pendingConflictCount.value = 0
    loaded.value = false
    return
  }
  await loadConversation(value)
}

function onSemesterChange() {
  selectedCourse.value = ''
  selectedClass.value = ''
}

function onCourseChange() {
  const course = courses.value.find((item) => item.id === Number(selectedCourse.value))
  selectedClass.value = course?.class_name ?? ''
}

async function ask() {
  if (!question.value.trim() || loading.value) return
  loading.value = true
  error.value = ''
  try {
    if (!conversation.value) {
      conversation.value = await qaApi.createConversation(currentScope.value, mode.value)
      conversations.value.unshift(conversation.value)
    } else if (scopeChanged.value) {
      // 先切换会话范围再提问；切换只影响后续追问，旧回答仍保留各自的范围快照。
      conversation.value = await qaApi.switchScope(conversation.value.id, currentScope.value)
    }
    await qaApi.sendMessage(conversation.value.id, question.value.trim())
    question.value = ''
    // 丢弃发送响应，重新读取会话，确保引用状态、覆盖提示与冲突数量是最新快照。
    await loadConversation(conversation.value.id)
  } catch (cause) {
    error.value = errorMessage(cause)
  } finally {
    loading.value = false
  }
}

// 失效引用没有可验证的原文位置，禁止跳转到资料库，避免把旧摘录当成当前内容。
function openCitation(citation: QaCitation) {
  if (!citation.valid) return
  router.push({
    path: '/library',
    query: {
      material: String(citation.material_id),
      version: String(citation.version_no),
      chunk: String(citation.source_chunk_id),
    },
  })
}

function coverageLabel(kind: QaCoverage['kind']) {
  return { partial: '部分覆盖', unavailable: '索引不可用', unindexed: '尚未索引' }[kind]
}

onMounted(loadOptions)
</script>

<template>
  <main class="view">
    <header class="page-heading">
      <div>
        <p class="eyebrow">M4 · 带引用的问答</p>
        <h1>智能问答</h1>
        <p>按学期、课程和教学班检索已索引资料；回答与引用分开显示，引用失效后不再计为可验证依据。</p>
      </div>
      <div class="heading-actions">
        <label>排序方式
          <select v-model="mode" :disabled="loading">
            <option value="hybrid">混合</option>
            <option value="keyword">关键词</option>
            <option value="vector">向量</option>
          </select>
        </label>
        <label>历史会话
          <select :value="conversation?.id ?? ''" :disabled="loading" @change="selectConversation">
            <option value="">新会话</option>
            <option v-for="item in conversations" :key="item.id" :value="item.id">
              会话 #{{ item.id }}
            </option>
          </select>
        </label>
      </div>
    </header>

    <ApiFeedback v-if="error" :message="error" :busy="loading" @retry="loadOptions" />

    <section class="panel">
      <div class="section-heading">
        <div><p class="eyebrow">检索范围</p><h2>选择课程资料范围</h2></div>
        <span v-if="conversation" class="status-pill">会话 #{{ conversation.id }}</span>
      </div>
      <div class="form-grid form-grid-3">
        <label>学期
          <select v-model="selectedSemester" :disabled="loading" @change="onSemesterChange">
            <option value="">不限学期</option>
            <option v-for="semester in semesters" :key="semester.id" :value="semester.id">
              {{ semester.name }}{{ semester.archived ? '（已归档）' : '' }}
            </option>
          </select>
        </label>
        <label>课程
          <select v-model="selectedCourse" :disabled="loading" @change="onCourseChange">
            <option value="">不限课程</option>
            <option v-for="course in filteredCourses" :key="course.id" :value="course.id">
              {{ course.name }}{{ course.code ? ` · ${course.code}` : '' }}
            </option>
          </select>
        </label>
        <label>教学班
          <select v-model="selectedClass" :disabled="loading">
            <option value="">不限教学班</option>
            <option v-for="name in classOptions" :key="name" :value="name">{{ name }}</option>
          </select>
        </label>
      </div>
      <p class="form-help">范围由服务端再次校验；未标清所选范围的资料不会参与回答。切换范围只影响后续追问，旧回答仍标记原范围。</p>
    </section>

    <!-- 旧轮次引用会因来源删除、归档或换版而失效；这里只提示，不改写历史回答。 -->
    <section v-if="invalidCitationCount" class="panel warning-panel" role="status">
      <strong>有 {{ invalidCitationCount }} 条旧引用已失效</strong>
      <p>来源可能已删除、归档或产生新版本，不能再作为可验证依据。请重新检索，或到资料库补充资料。</p>
      <div class="inline-actions">
        <RouterLink class="button-secondary" to="/library">打开资料库</RouterLink>
      </div>
    </section>

    <section v-if="pendingConflictCount" class="panel warning-panel" role="status">
      <strong>当前范围有 {{ pendingConflictCount }} 项待核对冲突</strong>
      <p>不同来源对同一字段给出不一致取值，系统没有自动选择生效版本。</p>
      <RouterLink class="button-secondary" to="/review">进入待核对</RouterLink>
    </section>

    <section class="panel">
      <div class="section-heading">
        <div><p class="eyebrow">对话</p><h2>{{ turns.length ? '问答记录' : '开始提问' }}</h2></div>
        <button v-if="conversation" type="button" class="text-button" :disabled="loading" @click="loadConversation(conversation.id)">
          刷新来源状态
        </button>
      </div>

      <div v-if="loading && !turns.length" class="compact-empty">正在检索资料并生成回答…</div>
      <div v-else-if="!turns.length" class="compact-empty">
        还没有问答记录。选择范围后提问，例如“第三次作业什么时候截止？”
      </div>

      <div v-else class="qa-turns">
        <article v-for="turn in turns" :key="turn.id" class="qa-turn">
          <header>
            <p class="qa-question">{{ turn.question }}</p>
            <span class="status-pill">{{ turn.intent }}</span>
          </header>

          <!-- insufficient 表示当前范围没有可用依据，只能说明“未发现”；不能据此推断“没有作业／没有冲突”。 -->
          <div v-if="turn.insufficient" class="qa-empty" role="status">
            <strong>现有资料中未发现足以回答的依据</strong>
            <p>{{ turn.answer }}</p>
            <p class="muted">这不等于“没有作业”或“没有冲突”；可以补充资料或进入待核对检查已提取事项。</p>
            <div class="inline-actions">
              <RouterLink class="button-secondary" to="/library">补充资料</RouterLink>
              <RouterLink class="button-secondary" to="/review">查看待核对</RouterLink>
            </div>
          </div>
          <p v-else class="qa-answer">{{ turn.answer }}</p>

          <!-- 覆盖提示区分部分覆盖、索引不可用与尚未索引；部分识别失败必须对用户可见。 -->
          <ul v-if="turn.coverage.length" class="notice-list">
            <li v-for="notice in turn.coverage" :key="`${notice.kind}-${notice.material_id}`" class="notice-item" :data-kind="notice.kind">
              <span class="status-pill" :data-status="notice.kind === 'partial' ? 'partial' : 'failed'">{{ coverageLabel(notice.kind) }}</span>
              <span><strong>{{ notice.title }}</strong>：{{ notice.message }}</span>
            </li>
          </ul>

          <!-- 只有 valid 引用可点击定位；失效卡片保留展示原因，便于核对来源状态。 -->
          <div v-if="turn.citations.length" class="citation-grid">
            <button
              v-for="citation in turn.citations"
              :key="citation.id"
              type="button"
              class="citation-card"
              :class="{ 'citation-invalid': !citation.valid }"
              :disabled="!citation.valid"
              @click="openCitation(citation)"
            >
              <span class="citation-head">
                <strong>{{ citation.material_title }}</strong>
                <span class="status-pill" :data-status="citation.valid ? 'done' : 'failed'">{{ citation.valid ? '可验证' : '已失效' }}</span>
              </span>
              <span class="citation-location">{{ citation.location }} · 版本 #{{ citation.version_no }}</span>
              <span class="citation-excerpt">{{ citation.excerpt }}</span>
              <span v-if="citation.message" class="citation-message">{{ citation.message }}</span>
              <span v-else class="citation-hint">点击定位原文</span>
            </button>
          </div>

          <div v-if="turn.conflicts.length" class="conflict-list">
            <p v-for="conflict in turn.conflicts" :key="conflict.code" class="conflict-item">
              <span class="status-pill" data-status="partial">待核对冲突</span>
              {{ conflict.message }}
            </p>
          </div>

          <ul v-if="turn.uncertainties.length" class="uncertainty-list">
            <li v-for="item in turn.uncertainties" :key="item">{{ item }}</li>
          </ul>
        </article>
      </div>

      <form class="qa-composer" @submit.prevent="ask">
        <label class="sr-only" for="qa-question">问题</label>
        <textarea
          id="qa-question"
          v-model="question"
          rows="3"
          maxlength="2000"
          placeholder="例如：CS101 第三次作业什么时候截止？"
          :disabled="loading"
        />
        <div class="composer-actions">
          <span class="muted">回答只使用当前范围内的已索引资料；模型服务失败时不会返回答案或引用。</span>
          <button type="submit" :disabled="loading || !question.trim()">{{ loading ? '正在生成…' : '提问' }}</button>
        </div>
      </form>
    </section>

    <section v-if="loaded && coverage.length" class="panel">
      <div class="section-heading"><div><p class="eyebrow">会话覆盖</p><h2>未覆盖范围汇总</h2></div></div>
      <ul class="notice-list">
        <li v-for="notice in coverage" :key="`${notice.kind}-${notice.material_id}`" class="notice-item" :data-kind="notice.kind">
          <span class="status-pill" :data-status="notice.kind === 'partial' ? 'partial' : 'failed'">{{ coverageLabel(notice.kind) }}</span>
          <span><strong>{{ notice.title }}</strong>：{{ notice.message }}</span>
        </li>
      </ul>
    </section>
  </main>
</template>

<style scoped>
.heading-actions { display: flex; flex-wrap: wrap; gap: 12px; align-items: end; }
.heading-actions label { display: grid; gap: 4px; font-size: 13px; color: var(--muted); }
.qa-turns { display: grid; gap: 18px; }
.qa-turn { display: grid; gap: 10px; padding: 16px; border: 1px solid var(--line); border-radius: 12px; background: var(--paper-strong); }
.qa-turn header { display: flex; align-items: start; justify-content: space-between; gap: 12px; }
.qa-question { margin: 0; font-weight: 700; color: var(--ink); }
.qa-answer { margin: 0; white-space: pre-wrap; line-height: 1.7; }
.qa-empty { display: grid; gap: 8px; padding: 14px; border: 1px dashed var(--line-dashed); border-radius: 10px; background: var(--surface-muted); }
.qa-empty p { margin: 0; }
.citation-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 10px; }
.citation-card { display: grid; gap: 6px; padding: 12px; border: 1px solid var(--brand-border); border-radius: 10px; background: var(--brand-selected); text-align: left; cursor: pointer; }
.citation-card:hover:not(:disabled) { border-color: var(--brand); }
.citation-card:disabled { cursor: not-allowed; opacity: .75; }
.citation-invalid { border-color: var(--danger-border); background: var(--danger-bg-soft); }
.citation-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.citation-location { color: var(--muted-strong); font-size: 12px; }
.citation-excerpt { color: var(--ink); font-size: 13px; line-height: 1.6; }
.citation-hint { color: var(--brand); font-size: 12px; font-weight: 700; }
.citation-message { color: var(--danger-ink); font-size: 12px; }
.notice-list, .uncertainty-list { margin: 0; padding: 0; list-style: none; display: grid; gap: 6px; }
.notice-item { display: flex; gap: 8px; align-items: start; padding: 8px 10px; border-radius: 8px; background: var(--warn-bg-soft); color: var(--warn-ink); font-size: 13px; }
.notice-item[data-kind="unavailable"] { background: var(--danger-bg-soft); color: var(--danger-ink); }
.conflict-list { display: grid; gap: 6px; }
.conflict-item { display: flex; gap: 8px; align-items: start; margin: 0; padding: 8px 10px; border-radius: 8px; background: var(--warn-bg-soft); color: var(--warn-ink); font-size: 13px; }
.uncertainty-list li { padding: 6px 10px; border-left: 3px solid var(--accent-border); color: var(--muted-strong); font-size: 13px; }
.warning-panel { border-color: var(--warn-border); background: var(--warn-bg); }
.inline-actions { display: flex; gap: 8px; flex-wrap: wrap; }
.qa-composer { display: grid; gap: 8px; margin-top: 16px; }
.qa-composer textarea { width: 100%; resize: vertical; }
.composer-actions { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
.sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); }
</style>
