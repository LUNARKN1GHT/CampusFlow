import { apiRequest } from './client'

export async function checkApiHealth(): Promise<void> {
  const data: unknown = await apiRequest('/health')
  if (
    typeof data !== 'object' || data === null ||
    !('status' in data) || data.status !== 'ok' ||
    !('service' in data) || data.service !== 'campusflow-api'
  ) {
    throw new Error('Unexpected health response')
  }
}
