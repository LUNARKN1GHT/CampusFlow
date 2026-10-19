import { apiRequest } from './client'

/**
 * 仅探测 API 进程存活：校验响应体的 status／service 字段，避免把代理页或 HTML 当成健康。
 * 只表示进程可访问，不代表数据库就绪或业务草案可用；结构不符时抛普通 Error。
 */
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
