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
  <main>
    <p class="eyebrow">CAMPUSFLOW / 开发骨架</p>
    <h1>让学习安排，<br />有据可循。</h1>
    <p class="intro">连接课程资料、明确学习任务，为下一步行动做好准备。</p>
    <section aria-labelledby="connection-title">
      <h2 id="connection-title">开发环境连接</h2>
      <p role="status" aria-live="polite" :class="['status', state]">{{ statusText }}</p>
      <p v-if="state === 'unavailable'">请按开发文档启动 Python 后端，再重新检查。</p>
      <p v-else>此检查仅验证 API 进程可访问，不代表业务功能或数据库已就绪。</p>
      <button :disabled="state === 'checking'" @click="refresh">重新检查</button>
    </section>
    <p class="note">当前已搭建前后端连接。资料导入、问答与学习计划将在后续迭代实现。</p>
  </main>
</template>
