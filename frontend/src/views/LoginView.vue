<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { errorMessage } from '../api/client'
import { useAuth } from '../composables/useAuth'

const route = useRoute()
const router = useRouter()
const { login } = useAuth()
const username = ref('student')
const password = ref('')
const busy = ref(false)
const error = ref('')
const expired = computed(() => route.query.expired === '1')

async function submit() {
  busy.value = true
  error.value = ''
  try {
    await login(username.value, password.value)
    const redirect = typeof route.query.redirect === 'string' && route.query.redirect.startsWith('/')
      ? route.query.redirect
      : '/'
    await router.replace(redirect)
  } catch (caught) {
    error.value = errorMessage(caught)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <main class="login-page">
    <section class="login-card" aria-labelledby="login-title">
      <div class="login-brand"><span class="brand-mark">CF</span><strong>CampusFlow</strong></div>
      <p class="eyebrow">个人工作空间</p>
      <h1 id="login-title">欢迎回来</h1>
      <p class="intro">登录后继续管理你的课程、任务与时间。</p>
      <p v-if="expired" class="inline-notice" role="status">会话已失效，请重新登录。私有页面内容已清除。</p>
      <form @submit.prevent="submit">
        <label>用户名<input v-model.trim="username" name="username" autocomplete="username" required /></label>
        <label>密码<input v-model="password" type="password" name="password" autocomplete="current-password" required /></label>
        <p v-if="error" class="form-error" role="alert">{{ error }}</p>
        <button type="submit" :disabled="busy">{{ busy ? '正在登录…' : '登录' }}</button>
      </form>
      <p class="note">本地默认账号见开发文档；共享环境请先修改默认凭据。</p>
    </section>
  </main>
</template>
