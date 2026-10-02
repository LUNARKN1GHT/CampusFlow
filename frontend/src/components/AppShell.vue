<script setup lang="ts">
const availableItems = [
  { label: '总览', to: '/' },
  { label: '课程', to: '/courses' },
  { label: '任务与计划', to: '/tasks' },
  { label: '设置', to: '/settings' },
]

const futureItems = ['资料库', '待核对', '智能问答']
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
        <RouterLink v-for="item in availableItems" :key="item.to" class="nav-item" :to="item.to">
          {{ item.label }}
        </RouterLink>
        <p class="nav-label nav-label-spaced">后续阶段</p>
        <span v-for="item in futureItems" :key="item" class="nav-item nav-item-disabled">
          {{ item }}<small>尚未开放</small>
        </span>
      </nav>

      <p class="sidebar-note">所有业务数据均来自真实 API；尚未实现的能力不会展示为可用入口。</p>
    </aside>

    <div class="content-column">
      <header class="topbar">
        <div>
          <p class="eyebrow">个人工作空间</p>
          <p class="topbar-title">把重要的学习事项放在一处</p>
        </div>
        <span class="stage-badge">M1 · 手动管理</span>
      </header>
      <RouterView />
    </div>
  </div>
</template>
