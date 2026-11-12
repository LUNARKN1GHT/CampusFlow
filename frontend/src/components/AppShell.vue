<script setup lang="ts">
// 受保护页面主布局：顶部单行导航（品牌／横向导航／主题与账号操作）；导航只列已实现入口，未开放能力不显示为可点击项。
import { useRouter } from 'vue-router'

import { useAuth } from '../composables/useAuth'
import { useTheme } from '../composables/useTheme'

const router = useRouter()
const { state, logout } = useAuth()
// 深浅模式只在顶部切换；设置页只保留色系选择，避免两处重复入口。
const { options: themeOptions, mode: themeMode, setThemeMode } = useTheme()
// active-class 置空、只用 exact-active-class：子路径页不会同时高亮父级入口。
const availableItems = [
  { label: '总览', to: '/' },
  { label: '课程', to: '/courses' },
  { label: '资料库', to: '/library' },
  { label: '任务', to: '/tasks' },
  { label: '学习计划', to: '/plan' },
  { label: '提醒', to: '/reminders' },
  { label: '导出', to: '/export' },
  { label: '设置', to: '/settings' },
]

async function signOut() {
  // logout 即使请求失败也会清空本地会话；replace 避免回退到受保护页面。
  await logout()
  await router.replace('/login')
}
</script>

<template>
  <div class="app-frame">
    <header class="app-header">
      <RouterLink class="brand" to="/" aria-label="CampusFlow 总览">
        <span class="brand-mark">CF</span>
        <span><strong>CampusFlow</strong><small>学习事务工作台</small></span>
      </RouterLink>

      <nav class="top-nav" aria-label="主要导航">
        <RouterLink v-for="item in availableItems" :key="item.to" class="nav-item" :to="item.to" active-class="" exact-active-class="router-link-active">
          {{ item.label }}
        </RouterLink>
      </nav>

      <div class="header-actions">
        <div class="theme-switch" role="group" aria-label="深浅模式">
          <button
            v-for="option in themeOptions"
            :key="option.value"
            type="button"
            class="theme-switch-item"
            :class="{ 'is-active': themeMode === option.value }"
            :aria-pressed="themeMode === option.value"
            @click="setThemeMode(option.value)"
          >
            {{ option.label }}
          </button>
        </div>
        <span class="user-badge">{{ state.session?.username }}</span>
        <button type="button" class="text-button" @click="signOut">退出</button>
      </div>
    </header>

    <RouterView />
  </div>
</template>
