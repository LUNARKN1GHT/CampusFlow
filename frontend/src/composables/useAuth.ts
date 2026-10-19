import { reactive } from 'vue'

import { authApi, type Session } from '../api/auth'
import { SESSION_EXPIRED_EVENT } from '../api/client'

// 全局登录状态（模块级单例）：会话只保存在内存，刷新后由路由守卫调用 checkSession 重新确认。
// 会话失效事件统一清理状态并跳转登录页；并发检查共享同一个请求，避免路由切换时重复调用。
type AuthState = {
  session: Session | null
  checked: boolean
}

const state = reactive<AuthState>({ session: null, checked: false })
let pendingCheck: Promise<boolean> | null = null

async function checkSession(): Promise<boolean> {
  // checked 为真后直接使用缓存结果；登录／登出会把它重新置为已确认。
  if (state.checked) return state.session !== null
  if (pendingCheck) return pendingCheck
  pendingCheck = authApi.session()
    .then((session) => {
      state.session = session
      return true
    })
    .catch(() => {
      state.session = null
      return false
    })
    .finally(() => {
      state.checked = true
      pendingCheck = null
    })
  return pendingCheck
}

async function login(username: string, password: string) {
  state.session = await authApi.login(username, password)
  state.checked = true
}

async function logout() {
  try {
    await authApi.logout()
  } finally {
    // 请求失败也要清空本地状态；跳转登录页由调用方（AppShell）负责。
    state.session = null
    state.checked = true
  }
}

// 监听注册在模块加载时，不依赖组件挂载；收到 401 事件即视为会话结束。
if (typeof window !== 'undefined') {
  window.addEventListener(SESSION_EXPIRED_EVENT, () => {
    state.session = null
    state.checked = true
    window.location.assign('/login?expired=1')
  })
}

export function useAuth() {
  return { state, checkSession, login, logout }
}
