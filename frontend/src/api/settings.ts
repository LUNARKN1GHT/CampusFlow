import { apiRequest } from './client'
import type { WorkspaceSettings } from './types'

export type WorkspaceSettingsInput = Omit<WorkspaceSettings, 'workspace_id'>

// 设置是工作空间级、全量 PUT：时区影响固定日程、计划区间与导出的解释，不是本机显示偏好。
export const settingsApi = {
  get: () => apiRequest<WorkspaceSettings>('/settings'),
  update: (input: WorkspaceSettingsInput) => apiRequest<WorkspaceSettings>('/settings', {
    method: 'PUT',
    body: JSON.stringify(input),
  }),
}
