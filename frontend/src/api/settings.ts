import { apiRequest } from './client'
import type { AiSettingsStatus, TextProvider, WorkspaceSettings } from './types'

export type WorkspaceSettingsInput = Omit<WorkspaceSettings, 'workspace_id'>

// 设置是工作空间级、全量 PUT：时区影响固定日程、计划区间与导出的解释，不是本机显示偏好。
export const settingsApi = {
  get: () => apiRequest<WorkspaceSettings>('/settings'),
  update: (input: WorkspaceSettingsInput) => apiRequest<WorkspaceSettings>('/settings', {
    method: 'PUT',
    body: JSON.stringify(input),
  }),
}

// 模型密钥只在桌面版由应用内保存；请求与响应都不携带密钥回显。
export type AiSettingsUpdate = {
  text_provider?: TextProvider
  zhipu_api_key?: string
  deepseek_api_key?: string
  clear_zhipu_api_key?: boolean
  clear_deepseek_api_key?: boolean
}

export const aiSettingsApi = {
  get: () => apiRequest<AiSettingsStatus>('/ai-settings'),
  update: (input: AiSettingsUpdate) => apiRequest<AiSettingsStatus>('/ai-settings', {
    method: 'PUT',
    body: JSON.stringify(input),
  }),
}
