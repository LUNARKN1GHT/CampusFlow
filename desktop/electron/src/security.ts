import { app, shell, type Session, type WebContents } from 'electron'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

/**
 * 应用页面的严格 CSP。`script-src 'self'` 之外只额外允许前端
 * index.html 中防止主题闪回的固定内联脚本（按内容哈希，不使用 unsafe-inline）。
 * 远程脚本、远程样式、远程连接全部禁止。
 *
 * 维护提示：若 `frontend/index.html` 的内联主题脚本内容变化，必须重新计算
 * sha256 哈希并同步到这里，否则主题脚本会被浏览器拒绝执行（页面仍可打开）。
 */
export const APP_CSP = [
  "default-src 'self'",
  "base-uri 'none'",
  "object-src 'none'",
  "frame-ancestors 'none'",
  "form-action 'self'",
  "script-src 'self' 'sha256-hVYUPydLbPwu2Cvamex068lADtyNsi4XkJfe6bGXI5M='",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob:",
  "font-src 'self' data:",
  "connect-src 'self'",
  "media-src 'self' blob:",
  "worker-src 'self' blob:",
  "manifest-src 'self'",
].join('; ')

export function isLoopbackHttpUrl(value: string): boolean {
  try {
    const url = new URL(value)
    if (url.protocol !== 'http:') return false
    return ['127.0.0.1', 'localhost', '[::1]', '::1'].includes(url.hostname)
  } catch {
    return false
  }
}

export function isSameOrigin(value: string, origin: string): boolean {
  try {
    return new URL(value).origin === origin
  } catch {
    return false
  }
}

export function isLocalRendererFile(value: string, rendererDir: string): boolean {
  try {
    const url = new URL(value)
    if (url.protocol !== 'file:') return false
    const filePath = path.resolve(fileURLToPath(url))
    const relative = path.relative(path.resolve(rendererDir), filePath)
    return relative !== '' && !relative.startsWith('..') && !path.isAbsolute(relative)
  } catch {
    return false
  }
}

/** 外链只允许显式 HTTPS 来源；未知页面一律拒绝，不进入 BrowserWindow。 */
export function isAllowedExternalUrl(value: string, allowlist: string[]): boolean {
  try {
    const url = new URL(value)
    if (url.protocol !== 'https:') return false
    if (url.username || url.password) return false
    return allowlist.includes(url.origin)
  } catch {
    return false
  }
}

export interface SecurityOptions {
  targetSession: Session
  rendererDir: string
  allowlist: string[]
  getAppOrigin: () => string | null
  openExternal?: (url: string) => Promise<void>
  onBlockedNavigation?: (url: string) => void
}

/**
 * 安装会话级与内容级安全策略：CSP 响应头、权限拒绝、导航白名单、
 * 新窗口拒绝与外链白名单（OAuth 交给系统浏览器）。
 */
export function installSecurity(options: SecurityOptions): void {
  const { targetSession, rendererDir, allowlist, getAppOrigin } = options
  const openExternal = options.openExternal ?? ((url: string) => shell.openExternal(url))

  targetSession.webRequest.onHeadersReceived((details, callback) => {
    const responseHeaders = { ...(details.responseHeaders ?? {}) }
    if (isLoopbackHttpUrl(details.url)) {
      for (const key of Object.keys(responseHeaders)) {
        if (key.toLowerCase() === 'content-security-policy') delete responseHeaders[key]
      }
      responseHeaders['Content-Security-Policy'] = [APP_CSP]
    }
    callback({ responseHeaders })
  })

  targetSession.setPermissionRequestHandler((_contents, _permission, callback) => {
    callback(false)
  })
  targetSession.setPermissionCheckHandler(() => false)

  const openIfAllowed = (url: string): void => {
    if (isAllowedExternalUrl(url, allowlist)) {
      void openExternal(url).catch(() => {
        // 打开系统浏览器失败不改变应用状态。
      })
      return
    }
    options.onBlockedNavigation?.(url)
  }

  const guardContents = (contents: WebContents): void => {
    const guardNavigation = (event: Electron.Event, url: string): void => {
      const origin = getAppOrigin()
      if (origin && isSameOrigin(url, origin)) return
      if (isLocalRendererFile(url, rendererDir)) return
      event.preventDefault()
      openIfAllowed(url)
    }
    contents.on('will-navigate', guardNavigation)
    contents.on('will-redirect', guardNavigation)
    contents.setWindowOpenHandler(({ url }) => {
      // OAuth 等外部流程交给系统浏览器；任何新窗口都不在应用内创建。
      openIfAllowed(url)
      return { action: 'deny' }
    })
    contents.on('will-attach-webview', (event) => event.preventDefault())
  }

  app.on('web-contents-created', (_event, contents) => guardContents(contents))
}
