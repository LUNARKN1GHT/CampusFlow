<script setup lang="ts">
/*
 * 今日总览：只读汇总当日时间块、下一段安排、未来 7 天截止、未排入与冲突风险。
 * 数据来自最近一次已接受计划与任务统计；没有已接受计划时提示先生成并接受草案。
 * 页面不提供写操作，各入口只跳转到任务或计划页。
 */
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'

import { errorMessage } from '../api/client'
import { plansApi } from '../api/plans'
import type { PlanOverview } from '../api/plans'
import ApiFeedback from '../components/ApiFeedback.vue'
import { priorityLabels, progressLabels } from '../constants/labels'
import { formatDueDate, formatTime, formatZonedTime } from '../utils/datetime'

const overview = ref<PlanOverview | null>(null)
const loading = ref(true)
const error = ref('')

// 时间块按工作空间时区展示，与“今日”分区（overview.today）保持同一时区；
// 后端未给出时区时才退回浏览器本地时区，并在页面上说明该限制。
const timezone = computed(() => overview.value?.timezone ?? '')

function timeText(value: string): string {
  return timezone.value ? formatZonedTime(value, timezone.value) : formatTime(value)
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    overview.value = await plansApi.overview()
  } catch (caught) {
    error.value = errorMessage(caught)
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <main class="page-content">
    <div class="page-heading">
      <p class="eyebrow">今日总览</p>
      <h1>今天先做什么，<br />一眼看清。</h1>
      <p class="intro">今日安排、近期截止和计划风险集中展示；总览只读取数据，不修改计划。</p>
    </div>

    <ApiFeedback v-if="error" :message="error" @retry="load" />
    <section v-else-if="loading" class="panel compact-empty">正在读取今日安排…</section>

    <template v-else-if="overview">
      <!-- 未排入与冲突／风险数量只统计最近一次已接受计划，不包含未接受草案。 -->
      <section class="panel overview-metrics" aria-label="关键状态">
        <div>
          <span>今日时间块</span>
          <strong>{{ overview.today_blocks.length }}</strong>
        </div>
        <div>
          <span>近期截止任务</span>
          <strong>{{ overview.upcoming_tasks.length }}</strong>
        </div>
        <div>
          <span>未完整排入</span>
          <strong>{{ overview.unscheduled_count }}</strong>
        </div>
        <div>
          <span>冲突与风险</span>
          <strong>{{ overview.conflict_count }}</strong>
        </div>
      </section>

      <div class="two-column-layout">
        <section class="panel" aria-label="今日安排">
          <div class="section-heading">
            <div><p class="eyebrow">{{ overview.today }}</p><h2>今日安排</h2></div>
            <RouterLink class="nav-link" to="/plan">查看计划</RouterLink>
          </div>
          <p v-if="!overview.has_plan" class="compact-empty">
            尚无已接受计划。先到<RouterLink to="/plan">学习计划</RouterLink>生成并接受两周草案。
          </p>
          <template v-else>
            <p class="form-help">
              <template v-if="timezone">时间按工作空间时区 {{ timezone }} 显示。</template>
              <template v-else>未取得工作空间时区，时间按浏览器本地时区显示，可能与“今日”日期错位。</template>
            </p>
            <p v-if="overview.next_block" class="next-block">
              下一段：<strong>{{ timeText(overview.next_block.starts_at) }}–{{ timeText(overview.next_block.ends_at) }}</strong>
              {{ overview.next_block.title }}
            </p>
            <ul class="plain-list">
              <li v-for="block in overview.today_blocks" :key="block.id" class="overview-item">
                <div class="task-title-line">
                  <strong>{{ timeText(block.starts_at) }}–{{ timeText(block.ends_at) }}</strong>
                  <RouterLink v-if="block.task_id" class="item-link" :to="`/tasks?task=${block.task_id}`">{{ block.title }}</RouterLink>
                  <span v-else>{{ block.title }}</span>
                  <span class="status-pill">{{ block.minutes }} 分钟</span>
                  <span v-if="block.locked" class="status-pill">已锁定</span>
                </div>
                <p>{{ block.reason }}</p>
              </li>
              <li v-if="!overview.today_blocks.length" class="muted">今天没有安排的学习时间块。</li>
            </ul>
          </template>
        </section>

        <section class="panel" aria-label="近期任务">
          <div class="section-heading">
            <div><p class="eyebrow">未来 7 天</p><h2>近期截止</h2></div>
            <RouterLink class="nav-link" to="/tasks">全部任务</RouterLink>
          </div>
          <ul class="plain-list">
            <li v-for="task in overview.upcoming_tasks" :key="task.id" class="overview-item">
              <div class="task-title-line">
                <strong>{{ formatDueDate(task.due_date, task.due_time) }}</strong>
                <span v-if="task.overdue" class="status-pill overdue-pill">逾期</span>
              </div>
              <p><RouterLink class="item-link" :to="`/tasks?task=${task.id}`">{{ task.title }}</RouterLink></p>
              <p class="form-help">优先级 {{ priorityLabels[task.priority] ?? task.priority }} · {{ progressLabels[task.progress] ?? task.progress }}</p>
            </li>
            <li v-if="!overview.upcoming_tasks.length" class="muted">未来 7 天没有带截止日期的待办任务。</li>
          </ul>
        </section>
      </div>

      <section class="panel quick-links" aria-label="下一步">
        <h2>下一步</h2>
        <div class="page-actions">
          <RouterLink class="button-link" to="/plan">调整两周计划</RouterLink>
        </div>
      </section>
    </template>
  </main>
</template>

<style scoped>
.overview-metrics { display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 14px; }
.overview-metrics div { display: grid; gap: 4px; padding: 12px; border: 1px solid var(--line); border-radius: 10px; background: var(--surface-muted); }
.overview-metrics span { color: var(--muted); font-size: 12px; }
.overview-metrics strong { font-size: 24px; }
.plain-list { margin: 0; padding: 0; list-style: none; display: grid; gap: 12px; }
.overview-item { padding-bottom: 12px; border-bottom: 1px solid var(--line); }
.overview-item:last-child { padding-bottom: 0; border-bottom: 0; }
.overview-item p { margin: 3px 0; color: var(--muted); font-size: 13px; }
.item-link { color: var(--brand); text-underline-offset: 3px; }
.item-link:hover { color: var(--ink); }
.next-block { padding: 12px; border-radius: 10px; background: var(--brand-soft); color: var(--brand); }
.quick-links { display: grid; gap: 12px; }
.page-actions { display: flex; gap: 10px; flex-wrap: wrap; }
</style>
