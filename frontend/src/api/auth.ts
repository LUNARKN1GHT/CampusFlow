// 本机会话接口：会话依靠 Cookie 维持，前端不保存令牌。
// 这些路径以 /auth/ 开头，401 按登录失败提示，不触发全局"会话失效"跳转。

import { apiRequest } from './client'

export type Session = { username: string }

export const authApi = {
  session: () => apiRequest<Session>('/auth/session'),
  login: (username: string, password: string) => apiRequest<Session>('/auth/login', {
    method: 'POST',
    body: JSON.stringify({ username, password }),
  }),
  // 登出返回 204，apiRequest 会解析为 undefined。
  logout: () => apiRequest<void>('/auth/logout', { method: 'POST' }),
}
