<script setup lang="ts">
import { useRouter } from 'vue-router'

import { useAuth } from '../composables/useAuth'

const router = useRouter()
const { state, logout } = useAuth()
const availableItems = [
  { label: '总览', to: '/' },
  { label: '课程', to: '/courses' },
  { label: '课程路径', to: '/course-paths' },
  { label: '资料库', to: '/library' },
  { label: '待核对', to: '/review' },
  { label: '版本对照', to: '/review/changes' },
  { label: '智能问答', to: '/qa' },
  { label: '任务', to: '/tasks' },
  { label: '学习计划', to: '/plan' },
  { label: '提醒', to: '/reminders' },
  { label: '导出', to: '/export' },
  { label: '外部日历', to: '/calendar' },
  { label: '设置', to: '/settings' },
]

const futureItems: string[] = []

async function signOut() {
  await logout()
  await router.replace('/login')
}
</script>

<template>
  <div class="app-frame">
    <aside class="sidebar">
      <RouterLink class="brand" to="/" aria-label="CampusFlow 总览">
        <span class="brand-mark">CF</span>
        <span><strong>CampusFlow</strong><small>学习事务工作台</small></span>
      </RouterLink>

      <nav aria-label="主要导航">
        <p class="nav-label">当前阶段</p>
        <RouterLink v-for="item in availableItems" :key="item.to" class="nav-item" :to="item.to" active-class="" exact-active-class="router-link-active">
          {{ item.label }}
        </RouterLink>
        <template v-if="futureItems.length">
          <p class="nav-label nav-label-spaced">后续阶段</p>
          <span v-for="item in futureItems" :key="item" class="nav-item nav-item-disabled">
            {{ item }}<small>尚未开放</small>
          </span>
        </template>
      </nav>

      <p class="sidebar-note">所有业务数据均来自真实 API；尚未实现的能力不会展示为可用入口。</p>
    </aside>

    <div class="content-column">
      <header class="topbar">
        <div>
          <p class="eyebrow">个人工作空间</p>
          <p class="topbar-title">把重要的学习事项放在一处</p>
        </div>
        <div class="topbar-actions">
          <span class="stage-badge">{{ state.session?.username }}</span>
          <button type="button" class="text-button" @click="signOut">退出</button>
        </div>
      </header>
      <RouterView />
    </div>
  </div>
</template>
