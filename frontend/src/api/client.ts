// 统一 HTTP 客户端：拼接 /api/v1、携带 Cookie 会话、默认 8 秒超时，并把失败归一为 ApiError。
// 错误分为 http／validation／network／timeout／invalid-response，调用方据此决定提示与重试方式。
const API_PREFIX = '/api/v1'
const SESSION_EXPIRED_EVENT = 'campusflow:session-expired'

type ValidationIssue = { loc?: Array<string | number>; msg?: string }
type ErrorPayload = { detail?: string | ValidationIssue[] }

/** 前端统一错误：status 为 null 表示没拿到响应（超时或网络不可达）。 */
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

/** 取 422 明细的首条，拼上字段路径（去掉 body 前缀），让用户知道改哪一项。 */
function validationMessage(issues: ValidationIssue[]): string {
  const first = issues[0]
  if (!first) return '提交内容不符合要求，请检查后重试。'
  const field = first.loc?.filter((part) => part !== 'body').join('.')
  return `${field ? `${field}：` : ''}${first.msg ?? '内容格式不正确'}`
}

/**
 * 把非 2xx 响应转成 ApiError：优先使用后端 detail 文案，非 JSON 错误页不泄露原始正文。
 * 401 只在 notifySessionExpiry 时广播会话失效事件；/auth/ 路径的 401 按登录失败提示。
 */
async function toApiError(response: Response, notifySessionExpiry: boolean): Promise<ApiError> {
  let payload: ErrorPayload | null = null
  try {
    payload = await response.json() as ErrorPayload
  } catch {
    // 非 JSON 错误页不向用户泄露原始响应正文。
  }

  if (response.status === 401) {
    if (notifySessionExpiry) window.dispatchEvent(new CustomEvent(SESSION_EXPIRED_EVENT))
    return new ApiError(
      notifySessionExpiry ? '登录已失效，请重新登录。' : '用户名或密码错误。',
      401,
      'http',
    )
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

/**
 * 发出请求并统一处理超时：GET 强制 no-store，避免浏览器缓存旧数据。
 * AbortError 归为超时，其余 fetch 异常归为网络不可达；两者都不会把请求伪装成成功。
 */
async function performRequest(
  path: string,
  init: RequestInit,
  headers: Headers,
  timeoutMs: number,
): Promise<Response> {
  const controller = new AbortController()
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs)
  try {
    const response = await fetch(`${API_PREFIX}${path}`, {
      ...init,
      headers,
      credentials: 'include',
      cache: init.method === undefined || init.method === 'GET' ? 'no-store' : init.cache,
      signal: controller.signal,
    })
    if (!response.ok) throw await toApiError(response, !path.startsWith('/auth/'))
    return response
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

/** 普通 JSON 接口：204 返回 undefined，响应不是 JSON 时报 invalid-response 而不是当作成功。 */
export async function apiRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  headers.set('Accept', 'application/json')
  // FormData 由浏览器自行设置 multipart 边界，不能强制覆盖为 JSON。
  if (init.body !== undefined && !(init.body instanceof FormData)) {
    headers.set('Content-Type', 'application/json')
  }

  const response = await performRequest(path, init, headers, 8000)
  if (response.status === 204) return undefined as T
  try {
    return await response.json() as T
  } catch {
    throw new ApiError('服务返回了无法识别的数据。', response.status, 'invalid-response')
  }
}

/** 读取二进制原文（PDF、图片）。原文缺失时抛出 ApiError，供调用方区分"已失效"；默认 20 秒超时。 */
export async function apiRequestBlob(path: string, timeoutMs = 20000): Promise<Blob> {
  const headers = new Headers({ Accept: '*/*' })
  const response = await performRequest(path, {}, headers, timeoutMs)
  return response.blob()
}

export type RequestedFile = { blob: Blob; filename: string | null }

/** 从 Content-Disposition 读取建议文件名；不支持的写法返回 null，由调用方给出兜底名。 */
function filenameFromDisposition(value: string | null): string | null {
  if (!value) return null
  const encoded = /filename\*\s*=\s*UTF-8''([^;]+)/i.exec(value)
  if (encoded?.[1]) {
    try {
      return decodeURIComponent(encoded[1].trim().replace(/^"|"$/g, ''))
    } catch {
      // 编码损坏时继续尝试普通 filename。
    }
  }
  const quoted = /filename\s*=\s*"([^"]+)"/i.exec(value)
  if (quoted?.[1]) return quoted[1].trim()
  const plain = /filename\s*=\s*([^;]+)/i.exec(value)
  return plain?.[1]?.trim() || null
}

/**
 * POST 下载文件并读取响应头中的文件名；非 2xx 仍按 apiRequest 的规则抛出 ApiError。
 * 默认 30 秒超时，导出文件可能比普通接口慢。
 */
export async function apiRequestFile(
  path: string,
  init: RequestInit = {},
  timeoutMs = 30000,
): Promise<RequestedFile> {
  const headers = new Headers(init.headers)
  headers.set('Accept', '*/*')
  if (init.body !== undefined && !(init.body instanceof FormData)) {
    headers.set('Content-Type', 'application/json')
  }
  const response = await performRequest(path, init, headers, timeoutMs)
  return {
    blob: await response.blob(),
    filename: filenameFromDisposition(response.headers.get('Content-Disposition')),
  }
}

/** 只对 ApiError 展示后端文案；未知异常统一兜底，避免把内部细节暴露给用户。 */
export function errorMessage(error: unknown): string {
  return error instanceof ApiError ? error.message : '操作没有完成，请重试。'
}

export { SESSION_EXPIRED_EVENT }
