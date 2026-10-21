import { expect, test } from '@playwright/test'

import {
  attemptAutoLogin,
  autoLoginNoticeMessage,
  buildAutoLoginNoticeScript,
  buildAutoLoginScript,
  isLoginUrl,
  type AutoLoginPage,
} from '../src/auto-login'

const LOGIN_URL = 'http://127.0.0.1:43210/login?redirect=/plan'
const HOME_URL = 'http://127.0.0.1:43210/plan'

class FakePage implements AutoLoginPage {
  url: string
  /** 第几次提交后离开登录页；0 表示永不离开。 */
  navigateAfter: number
  submits = 0
  scripts: string[] = []
  failures: Error[] = []

  constructor(url = LOGIN_URL, navigateAfter = 1) {
    this.url = url
    this.navigateAfter = navigateAfter
  }

  async executeJavaScript(code: string): Promise<unknown> {
    this.scripts.push(code)
    const failure = this.failures.shift()
    if (failure) throw failure
    this.submits += 1
    if (this.navigateAfter > 0 && this.submits >= this.navigateAfter) {
      this.url = HOME_URL
    }
    return code.includes('input[name="username"]')
  }

  getURL(): string {
    return this.url
  }
}

function fakeClock(): { now: () => number; sleep: (ms: number) => Promise<void>; elapsed: () => number } {
  let clock = 0
  return {
    now: () => clock,
    sleep: async (ms: number) => {
      clock += ms
    },
    elapsed: () => clock,
  }
}

test('登录页首次提交后跳转，自动登录成功', async () => {
  const page = new FakePage()
  const clock = fakeClock()
  const logs: string[] = []
  const outcome = await attemptAutoLogin(
    page,
    { username: 'campusflow', password: 'local-login-token' },
    {
      logger: { info: (line) => logs.push(line), warn: (line) => logs.push(line) },
      now: clock.now,
      sleep: clock.sleep,
      navigationTimeoutMs: 500,
      pollIntervalMs: 50,
    },
  )
  expect(outcome).toBe('success')
  expect(page.submits).toBe(1)
  expect(page.scripts[0]).toContain('input[name="username"]')
  expect(page.scripts[0]).toContain('input[name="password"]')
  expect(page.scripts[0]).toContain('requestSubmit')
  expect(page.scripts[0]).not.toContain('localStorage')
  expect(logs.join('\n')).toContain('自动登录成功：campusflow')
})

test('第一次提交未跳转时等待 1 秒后重试一次', async () => {
  const page = new FakePage(LOGIN_URL, 2)
  const clock = fakeClock()
  const outcome = await attemptAutoLogin(
    page,
    { username: 'campusflow', password: 'local-login-token' },
    {
      now: clock.now,
      sleep: clock.sleep,
      navigationTimeoutMs: 400,
      pollIntervalMs: 100,
      retryDelayMs: 1_000,
    },
  )
  expect(outcome).toBe('success')
  expect(page.submits).toBe(2)
  expect(clock.elapsed()).toBeGreaterThanOrEqual(1_400)
})

test('两次提交仍未跳转时返回 failed，只提示用户名且不泄露口令', async () => {
  const page = new FakePage(LOGIN_URL, 0)
  const clock = fakeClock()
  const logs: string[] = []
  const outcome = await attemptAutoLogin(
    page,
    { username: 'campusflow', password: 'super-secret-token' },
    {
      logger: { info: (line) => logs.push(line), warn: (line) => logs.push(line) },
      now: clock.now,
      sleep: clock.sleep,
      navigationTimeoutMs: 300,
      pollIntervalMs: 100,
    },
  )
  expect(outcome).toBe('failed')
  expect(page.submits).toBe(2)
  const text = logs.join('\n')
  expect(text).toContain('自动登录未完成')
  expect(text).toContain('campusflow')
  expect(text).not.toContain('super-secret-token')
})

test('脚本异常信息中的口令也会被隐藏', async () => {
  const page = new FakePage(LOGIN_URL, 0)
  page.failures = [new Error('evaluate failed for super-secret-token')]
  const clock = fakeClock()
  const logs: string[] = []
  const outcome = await attemptAutoLogin(
    page,
    { username: 'campusflow', password: 'super-secret-token' },
    {
      logger: { info: (line) => logs.push(line), warn: (line) => logs.push(line) },
      now: clock.now,
      sleep: clock.sleep,
      navigationTimeoutMs: 200,
      pollIntervalMs: 100,
    },
  )
  expect(outcome).toBe('failed')
  const text = logs.join('\n')
  expect(text).toContain('***')
  expect(text).not.toContain('super-secret-token')
})

test('初始不是登录页时等待后不介入', async () => {
  const page = new FakePage(HOME_URL, 1)
  const clock = fakeClock()
  const outcome = await attemptAutoLogin(
    page,
    { username: 'campusflow', password: 'local-login-token' },
    { now: clock.now, sleep: clock.sleep, waitForLoginMs: 300, pollIntervalMs: 100 },
  )
  expect(outcome).toBe('not-required')
  expect(page.submits).toBe(0)
})

test('登录页判定与脚本生成不依赖站外地址', () => {
  expect(isLoginUrl(LOGIN_URL)).toBe(true)
  expect(isLoginUrl('http://127.0.0.1:43210/login')).toBe(true)
  expect(isLoginUrl(HOME_URL)).toBe(false)
  expect(isLoginUrl('not a url')).toBe(false)

  const script = buildAutoLoginScript({ username: 'a"b', password: 'p\\w' })
  expect(script).toContain(JSON.stringify('a"b'))
  expect(script).toContain(JSON.stringify('p\\w'))
})

test('失败提示只包含用户名，并在页面上非阻塞展示', () => {
  const message = autoLoginNoticeMessage('campusflow')
  expect(message).toContain('campusflow')
  expect(message).not.toContain('token')
  const script = buildAutoLoginNoticeScript('campusflow')
  expect(script).toContain(JSON.stringify(message))
  expect(script).toContain('data-testid')
  expect(script).toContain('document.body.prepend')
})

