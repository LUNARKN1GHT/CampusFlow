const API_PREFIX = '/api/v1'
const SESSION_EXPIRED_EVENT = 'campusflow:session-expired'

type ValidationIssue = { loc?: Array<string | number>; msg?: string }
type ErrorPayload = { detail?: string | ValidationIssue[] }

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number | null,
    public readonly kind: 'http' | 'validation' | 'network' | 'timeout' | 'invalid-response',
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

function validationMessage(issues: ValidationIssue[]): string {
  const first = issues[0]
  if (!first) return '提交内容不符合要求，请检查后重试。'
  const field = first.loc?.filter((part) => part !== 'body').join('.')
  return `${field ? `${field}：` : ''}${first.msg ?? '内容格式不正确'}`
}

async function toApiError(response: Response): Promise<ApiError> {
  let payload: ErrorPayload | null = null
  try {
    payload = await response.json() as ErrorPayload
  } catch {
    // 非 JSON 错误页不向用户泄露原始响应正文。
  }

  if (response.status === 401) {
    window.dispatchEvent(new CustomEvent(SESSION_EXPIRED_EVENT))
    return new ApiError('登录已失效，请重新登录。', 401, 'http')
  }
  if (response.status === 422 && Array.isArray(payload?.detail)) {
    return new ApiError(validationMessage(payload.detail), 422, 'validation')
  }
  if (typeof payload?.detail === 'string' && [400, 404, 409, 422].includes(response.status)) {
    return new ApiError(payload.detail, response.status, response.status === 422 ? 'validation' : 'http')
  }
  if (response.status === 404) return new ApiError('没有找到请求的内容，它可能已被删除。', 404, 'http')
  if (response.status >= 500) return new ApiError('服务暂时不可用，请稍后重试。', response.status, 'http')
  return new ApiError(`请求未完成（HTTP ${response.status}）。`, response.status, 'http')
}

export async function apiRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  const controller = new AbortController()
  const timeout = window.setTimeout(() => controller.abort(), 8000)
  const headers = new Headers(init.headers)
  headers.set('Accept', 'application/json')
  if (init.body !== undefined) headers.set('Content-Type', 'application/json')

  try {
    const response = await fetch(`${API_PREFIX}${path}`, {
      ...init,
      headers,
      credentials: 'include',
      cache: init.method === undefined || init.method === 'GET' ? 'no-store' : init.cache,
      signal: controller.signal,
    })
    if (!response.ok) throw await toApiError(response)
    if (response.status === 204) return undefined as T
    try {
      return await response.json() as T
    } catch {
      throw new ApiError('服务返回了无法识别的数据。', response.status, 'invalid-response')
    }
  } catch (error) {
    if (error instanceof ApiError) throw error
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new ApiError('请求超时，请检查网络后重试。', null, 'timeout')
    }
    throw new ApiError('无法连接服务，请检查网络或确认后端已经启动。', null, 'network')
  } finally {
    window.clearTimeout(timeout)
  }
}

export function errorMessage(error: unknown): string {
  return error instanceof ApiError ? error.message : '操作没有完成，请重试。'
}

export { SESSION_EXPIRED_EVENT }
