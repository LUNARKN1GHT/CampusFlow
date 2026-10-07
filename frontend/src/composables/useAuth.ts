import { reactive } from 'vue'

import { authApi, type Session } from '../api/auth'
import { SESSION_EXPIRED_EVENT } from '../api/client'

type AuthState = {
  session: Session | null
  checked: boolean
}

const state = reactive<AuthState>({ session: null, checked: false })
let pendingCheck: Promise<boolean> | null = null

async function checkSession(): Promise<boolean> {
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
    state.session = null
    state.checked = true
  }
}

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
