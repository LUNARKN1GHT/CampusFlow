<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { checkApiHealth } from '../api/health'

const state = ref<'checking' | 'connected' | 'unavailable'>('checking')
const statusText = computed(() => ({
  checking: '正在检查后端连接…',
  connected: '后端连接成功',
  unavailable: '暂时无法连接后端',
})[state.value])

async function refresh() {
  state.value = 'checking'
  try {
    await checkApiHealth()
    state.value = 'connected'
  } catch {
    state.value = 'unavailable'
  }
}

onMounted(refresh)
</script>

<template>
  <main class="page-content">
    <div class="page-heading hero-heading">
      <p class="eyebrow">今日总览</p>
      <h1>让学习安排，<br />有据可循。</h1>
      <p class="intro">连接课程、任务与时间，为下一步行动做好准备。</p>
    </div>
    <section aria-labelledby="connection-title">
      <h2 id="connection-title">开发环境连接</h2>
      <p role="status" aria-live="polite" :class="['status', state]">{{ statusText }}</p>
      <p v-if="state === 'unavailable'">请按开发文档启动 Python 后端，再重新检查。</p>
      <p v-else>此检查仅验证 API 进程可访问，不代表业务功能或数据库已就绪。</p>
      <button :disabled="state === 'checking'" @click="refresh">重新检查</button>
    </section>
    <p class="note">当前阶段提供手动学习事务管理；资料导入、问答与自动排期仍未开放。</p>
  </main>
</template>
