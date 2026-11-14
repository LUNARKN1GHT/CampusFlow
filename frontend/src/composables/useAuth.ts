import { reactive } from 'vue'

import { authApi, type Session } from '../api/auth'
import { ApiError, errorMessage, SESSION_EXPIRED_EVENT } from '../api/client'

// 全局登录状态（模块级单例）：会话只保存在内存，刷新后由路由守卫调用 checkSession 重新确认。
// 会话失效事件统一清理状态并交给路由层跳转登录页；并发检查共享同一个请求，避免路由切换时重复调用。
type AuthState = {
  session: Session | null
  checked: boolean
  // 最近一次会话检查的连接失败原因；只有 401 才算确定未登录，网络不可达或超时保留 checked = false 供重试。
  error: string
}

/** 会话失效后的跳转由路由层注册，useAuth 不直接依赖 router，避免两者形成循环依赖。 */
type SessionExpiredHandler = () => Promise<unknown> | void

const state = reactive<AuthState>({ session: null, checked: false, error: '' })
let pendingCheck: Promise<boolean> | null = null
let sessionExpiredHandler: SessionExpiredHandler | null = null
// 并发 401 去抖：跳转进行中只清理状态，不再重复导航。
let handlingExpiry = false

async function checkSession(): Promise<boolean> {
  // checked 为真后直接使用缓存结果；登录／登出会把它重新置为已确认。
  if (state.checked) return state.session !== null
  if (pendingCheck) return pendingCheck
  pendingCheck = authApi.session()
    .then((session) => {
      state.session = session
      state.error = ''
      state.checked = true
      return true
    })
    .catch((error: unknown) => {
      state.session = null
      // 只有 401 是确定的“未登录”；网络不可达或超时不能缓存成未登录，否则后端恢复后仍需刷新页面。
      if (error instanceof ApiError && error.status === 401) {
        state.error = ''
        state.checked = true
      } else {
        state.error = errorMessage(error)
      }
      return false
    })
    .finally(() => {
      pendingCheck = null
    })
  return pendingCheck
}

async function login(username: string, password: string) {
  state.session = await authApi.login(username, password)
  state.checked = true
  state.error = ''
}

async function logout() {
  try {
    await authApi.logout()
  } finally {
    // 请求失败也要清空本地状态；跳转登录页由调用方（AppShell）负责。
    state.session = null
    state.checked = true
    state.error = ''
  }
}

/** 由路由层注册会话失效跳转；传入 null 可解除注册。 */
function setSessionExpiredHandler(handler: SessionExpiredHandler | null) {
  sessionExpiredHandler = handler
}

/** 收到会话失效事件：先清理本地状态，再由路由层带原路径跳转登录页（整页刷新会丢失未保存内容）。 */
function handleSessionExpired() {
  state.session = null
  state.checked = true
  state.error = ''
  if (handlingExpiry) return
  const handler = sessionExpiredHandler
  if (!handler) return
  handlingExpiry = true
  Promise.resolve(handler())
    .catch(() => {
      // 跳转失败时保持已清理的会话状态，下一次导航仍会被守卫拦到登录页。
    })
    .finally(() => {
      handlingExpiry = false
    })
}

// 监听注册在模块加载时，不依赖组件挂载；收到 401 事件即视为会话结束。
if (typeof window !== 'undefined') {
  window.addEventListener(SESSION_EXPIRED_EVENT, handleSessionExpired)
}

export { setSessionExpiredHandler }

export function useAuth() {
  return { state, checkSession, login, logout }
}
