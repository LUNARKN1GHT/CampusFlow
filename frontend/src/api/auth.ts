import { apiRequest } from './client'

export type Session = { username: string }

export const authApi = {
  session: () => apiRequest<Session>('/auth/session'),
  login: (username: string, password: string) => apiRequest<Session>('/auth/login', {
    method: 'POST',
    body: JSON.stringify({ username, password }),
  }),
  logout: () => apiRequest<void>('/auth/logout', { method: 'POST' }),
}
