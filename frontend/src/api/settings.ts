import { apiRequest } from './client'
import type { WorkspaceSettings } from './types'

export type WorkspaceSettingsInput = Omit<WorkspaceSettings, 'workspace_id'>

export const settingsApi = {
  get: () => apiRequest<WorkspaceSettings>('/settings'),
  update: (input: WorkspaceSettingsInput) => apiRequest<WorkspaceSettings>('/settings', {
    method: 'PUT',
    body: JSON.stringify(input),
  }),
}
