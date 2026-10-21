import type { Logger } from './logger'

/**
 * 桌面自动登录：bootstrap 生成的本机凭据只在本模块内部使用。
 * 口令通过 `webContents.executeJavaScript` 填入登录表单，不经过 preload／IPC，
 * 也不写入 localStorage；失败时只提示用户名并保留登录页可用。
 */
export interface AutoLoginPage {
  executeJavaScript(code: string): Promise<unknown>
  getURL(): string
}

export interface AutoLoginCredentials {
  username: string
  password: string
}

export interface AutoLoginOptions {
  logger?: Pick<Logger, 'info' | 'warn'>
  sleep?: (ms: number) => Promise<void>
  now?: () => number
  /** 等待前端路由守卫把初始页面切到 /login 的时间。 */
  waitForLoginMs?: number
  /** 提交后等待 URL 离开 /login 的时间。 */
  navigationTimeoutMs?: number
  retryDelayMs?: number
  pollIntervalMs?: number
}

export type AutoLoginOutcome = 'success' | 'not-required' | 'failed'

const DEFAULT_WAIT_FOR_LOGIN_MS = 3_000
const DEFAULT_NAVIGATION_TIMEOUT_MS = 8_000
const DEFAULT_RETRY_DELAY_MS = 1_000
const DEFAULT_POLL_INTERVAL_MS = 150

export function isLoginUrl(url: string): boolean {
  try {
    return new URL(url).pathname === '/login'
  } catch {
    return false
  }
}

/**
 * 生成在页面执行的填充脚本。用原生 value setter 触发 Vue 的 v-model，
 * 再走表单自身的提交路径，避免绕过前端的校验与错误提示。
 */
export function buildAutoLoginScript(credentials: AutoLoginCredentials): string {
  const username = JSON.stringify(credentials.username)
  const password = JSON.stringify(credentials.password)
  return `(() => {
  const form = document.querySelector('form');
  const username = document.querySelector('input[name="username"]');
  const password = document.querySelector('input[name="password"]');
  if (!form || !username || !password) return false;
  const fill = (element, value) => {
    const descriptor = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(element), 'value');
    if (descriptor && typeof descriptor.set === 'function') descriptor.set.call(element, value);
    else element.value = value;
    element.dispatchEvent(new Event('input', { bubbles: true }));
    element.dispatchEvent(new Event('change', { bubbles: true }));
  };
  fill(username, ${username});
  fill(password, ${password});
  if (typeof form.requestSubmit === 'function') form.requestSubmit();
  else form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
  return true;
})()`
}

function describeError(error: unknown, password: string): string {
  const text = error instanceof Error ? error.message : String(error)
  const scrubbed = password ? text.split(password).join('***') : text
  return scrubbed.replace(/\s+/g, ' ').slice(0, 240)
}

/** 失败提示只包含用户名；不接触口令。 */
export function autoLoginNoticeMessage(username: string): string {
  return `自动登录未完成，请使用本机账号 ${username} 登录。`
}

/** 在登录页顶部插入非阻塞提示条；不覆盖表单，也不改动会话状态。 */
export function buildAutoLoginNoticeScript(username: string): string {
  return `(() => {
  if (document.querySelector('[data-testid="auto-login-notice"]')) return;
  const notice = document.createElement('p');
  notice.setAttribute('data-testid', 'auto-login-notice');
  notice.setAttribute('role', 'status');
  notice.textContent = ${JSON.stringify(autoLoginNoticeMessage(username))};
  notice.style.cssText =
    'margin:0;padding:10px 16px;text-align:center;font:14px/1.5 -apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif;' +
    'color:var(--warn-ink,#8a4b00);background:var(--warn-bg-soft,#fff7e6);border-bottom:1px solid var(--line,#f0c36d)';
  document.body.prepend(notice);
})()`
}

async function waitForNavigation(
  page: AutoLoginPage,
  options: {
    sleep: (ms: number) => Promise<void>
    now: () => number
    navigationTimeoutMs: number
    pollIntervalMs: number
  },
): Promise<boolean> {
  const deadline = options.now() + options.navigationTimeoutMs
  for (;;) {
    if (!isLoginUrl(page.getURL())) return true
    if (options.now() >= deadline) return false
    await options.sleep(options.pollIntervalMs)
  }
}

/**
 * 只在登录页执行：提交后轮询 URL 是否离开 /login；未离开时按约定 1 秒后重试一次。
 * 页面已不是登录页（已有会话或用户已登录）时返回 not-required。
 */
export async function attemptAutoLogin(
  page: AutoLoginPage,
  credentials: AutoLoginCredentials,
  options: AutoLoginOptions = {},
): Promise<AutoLoginOutcome> {
  const sleep =
    options.sleep ?? ((ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms)))
  const now = options.now ?? (() => Date.now())
  const pollIntervalMs = options.pollIntervalMs ?? DEFAULT_POLL_INTERVAL_MS
  const waitForLoginMs = options.waitForLoginMs ?? DEFAULT_WAIT_FOR_LOGIN_MS
  const navigationOptions = {
    sleep,
    now,
    pollIntervalMs,
    navigationTimeoutMs: options.navigationTimeoutMs ?? DEFAULT_NAVIGATION_TIMEOUT_MS,
  }
  const retryDelayMs = options.retryDelayMs ?? DEFAULT_RETRY_DELAY_MS
  const logger = options.logger

  if (!isLoginUrl(page.getURL())) {
    // 前端先检查会话再决定是否跳转 /login；等待路由稳定后仍不是登录页就不介入。
    const deadline = now() + waitForLoginMs
    while (now() < deadline && !isLoginUrl(page.getURL())) {
      await sleep(pollIntervalMs)
    }
    if (!isLoginUrl(page.getURL())) return 'not-required'
  }

  for (let attempt = 1; attempt <= 2; attempt += 1) {
    if (!isLoginUrl(page.getURL())) return 'success'
    try {
      const submitted = (await page.executeJavaScript(buildAutoLoginScript(credentials))) === true
      if (!submitted && !isLoginUrl(page.getURL())) return 'success'
    } catch (error) {
      logger?.warn(`自动登录脚本执行失败：${describeError(error, credentials.password)}`)
    }
    if (await waitForNavigation(page, navigationOptions)) {
      logger?.info(`自动登录成功：${credentials.username}`)
      return 'success'
    }
    if (attempt === 1) {
      logger?.warn('自动登录后页面仍停在登录页，1 秒后重试一次')
      await sleep(retryDelayMs)
    }
  }
  logger?.warn(`自动登录未完成，请使用本机账号 ${credentials.username} 手动登录`)
  return 'failed'
}
