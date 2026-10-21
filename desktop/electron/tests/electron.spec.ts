import { chmodSync, mkdtempSync, readFileSync, rmSync, existsSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'

import { expect, test, type ElectronApplication } from '@playwright/test'
import { _electron as electron } from 'playwright'

const APP_MAIN = path.join(__dirname, '..', 'dist', 'main.js')
const tempDirs: string[] = []
const openApps: ElectronApplication[] = []

interface LaunchedApp {
  app: ElectronApplication
  userData: string
}

async function launchStub(extraEnv: Record<string, string> = {}): Promise<LaunchedApp> {
  const userData = mkdtempSync(path.join(os.tmpdir(), 'campusflow-p008-e2e-'))
  tempDirs.push(userData)
  const app = await electron.launch({
    args: [APP_MAIN],
    env: {
      ...process.env,
      CAMPUSFLOW_ELECTRON_STUB: '1',
      CAMPUSFLOW_ELECTRON_USER_DATA: userData,
      CAMPUSFLOW_ELECTRON_STUB_DELAY_MS: '350',
      ...extraEnv,
    },
  })
  openApps.push(app)
  return { app, userData }
}

function readPid(userData: string, role: 'api' | 'worker'): number {
  const target = path.join(userData, 'run', `stub-${role}.pid`)
  return Number.parseInt(readFileSync(target, 'utf8').trim(), 10)
}

function isAlive(pid: number): boolean {
  try {
    process.kill(pid, 0)
    return true
  } catch (error) {
    return (error as NodeJS.ErrnoException).code === 'EPERM'
  }
}

async function quitGracefully(app: ElectronApplication): Promise<void> {
  const closed = app.waitForEvent('close')
  await app.evaluate(({ app: electronApp }) => electronApp.quit())
  await closed
}

async function quitWithSignal(app: ElectronApplication, signal: NodeJS.Signals): Promise<void> {
  const closed = app.waitForEvent('close')
  app.process().kill(signal)
  await closed
}

test.afterEach(async () => {
  // 测试失败时也确保 Electron 及其桩进程退出，避免污染后续用例。
  for (const app of openApps.splice(0)) {
    try {
      await app.close()
    } catch {
      // 已经退出的应用再次关闭会抛错，忽略即可。
    }
  }
  for (const dir of tempDirs.splice(0)) {
    rmSync(dir, { recursive: true, force: true })
  }
})

test('测试桩模式：启动页 → 主窗口，日志按阶段推进，退出后无残留子进程', async () => {
  const { app, userData } = await launchStub()
  const splash = await app.firstWindow()
  const mainPromise = app.waitForEvent('window', {
    predicate: (page) => page.url().startsWith('http://127.0.0.1:'),
  })

  expect(await splash.title()).toBe('CampusFlow')
  await expect(splash.locator('[data-testid="splash"]')).toBeVisible()
  await expect(splash.locator('[data-testid="splash-status"]')).not.toBeEmpty()

  const main = await mainPromise
  await expect(main.locator('[data-testid="app-ready"]')).toHaveText('CampusFlow 桌面版已就绪')
  expect(await main.title()).toBe('CampusFlow')
  expect(main.url()).toMatch(/^http:\/\/127\.0\.0\.1:\d+\//)

  // 安全基线：无 Node 原语，preload 只暴露最小接口，CSP 响应头生效。
  expect(await main.evaluate(() => typeof (window as any).require)).toBe('undefined')
  expect(await main.evaluate(() => typeof (window as any).process)).toBe('undefined')
  expect(await main.evaluate(() => typeof (window as any).campusflow?.getStatus)).toBe('function')
  const csp = await main.evaluate(async () => {
    const response = await fetch('/api/v1/health')
    return response.headers.get('content-security-policy')
  })
  expect(csp).toContain("script-src 'self'")
  expect(csp).not.toMatch(/script-src[^;]*unsafe-inline/)

  // OAuth 弹窗被替换为系统浏览器 + 占位对象；未列入白名单的来源不会真正打开。
  const popupType = await main.evaluate(() =>
    typeof window.open('https://example.com/oauth', 'campusflow-calendar-oauth'),
  )
  expect(popupType).toBe('object')
  expect(await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows().length)).toBe(1)

  await expect.poll(() => existsSync(path.join(userData, 'run', 'stub-api.pid'))).toBe(true)
  await expect.poll(() => existsSync(path.join(userData, 'run', 'stub-worker.pid'))).toBe(true)
  const apiPid = readPid(userData, 'api')
  const workerPid = readPid(userData, 'worker')
  expect(isAlive(apiPid)).toBe(true)
  expect(isAlive(workerPid)).toBe(true)

  const log = readFileSync(path.join(userData, 'logs', 'main.log'), 'utf8')
  const stageOffsets = ['postgres', 'migrate', 'api', 'worker'].map((stage) =>
    log.indexOf(`阶段开始：${stage}`),
  )
  for (const offset of stageOffsets) expect(offset).toBeGreaterThan(-1)
  expect(stageOffsets).toEqual([...stageOffsets].sort((left, right) => left - right))
  expect(log).toContain('启动完成：所有阶段就绪')
  // 测试桩模式跳过首启初始化：既不调用 sidecar bootstrap，也不出现 bootstrap 阶段。
  expect(log).not.toContain('阶段开始：bootstrap')

  await quitGracefully(app)
  await expect.poll(() => isAlive(apiPid), { timeout: 5_000 }).toBe(false)
  await expect.poll(() => isAlive(workerPid), { timeout: 5_000 }).toBe(false)
})

test('bootstrap 失败时显示错误页，且不启动 API', async () => {
  test.skip(process.platform === 'win32', '假 sidecar 使用 POSIX shell 脚本')
  const userData = mkdtempSync(path.join(os.tmpdir(), 'campusflow-p008-bootstrap-'))
  const binDir = mkdtempSync(path.join(os.tmpdir(), 'campusflow-p008-sidecar-'))
  tempDirs.push(userData, binDir)
  const fakeSidecar = path.join(binDir, 'fake-sidecar')
  writeFileSync(
    fakeSidecar,
    [
      '#!/bin/sh',
      'echo "初始化失败：无法写入数据目录（诊断日志目录：/tmp/cf-bootstrap-failed/logs）" >&2',
      'exit 1',
      '',
    ].join('\n'),
  )
  chmodSync(fakeSidecar, 0o755)

  const app = await electron.launch({
    args: [APP_MAIN],
    env: {
      ...process.env,
      CAMPUSFLOW_ELECTRON_USER_DATA: userData,
      CAMPUSFLOW_BOOTSTRAP: '1',
      CAMPUSFLOW_AUTO_LOGIN: '0',
      CAMPUSFLOW_SIDECAR_BIN: fakeSidecar,
    },
  })
  openApps.push(app)

  const shell = await app.firstWindow()
  await expect(shell.locator('[data-testid="error"]')).toBeVisible()
  await expect(shell.locator('[data-testid="error-stage"]')).toContainText('首启初始化')
  await expect(shell.locator('[data-testid="error-message"]')).toContainText('退出码 1')
  await expect(shell.locator('[data-testid="error-message"]')).toContainText('诊断日志目录')
  await expect(shell.locator('[data-testid="error-log-tail"]')).toContainText('阶段开始：bootstrap')

  const log = readFileSync(path.join(userData, 'logs', 'main.log'), 'utf8')
  expect(log).toContain('启动顺序：bootstrap → API → Worker')
  expect(log).toContain('阶段开始：bootstrap')
  expect(log).not.toContain('阶段开始：api')
  expect(log).not.toContain('阶段开始：postgres')
  expect(existsSync(path.join(userData, 'run', 'stub-api.pid'))).toBe(false)

  await quitGracefully(app)
})

test('模拟迁移失败时显示错误页，点击重试后进入主窗口', async () => {
  const { app, userData } = await launchStub({
    CAMPUSFLOW_ELECTRON_STUB_FAIL_STAGE: 'migrate',
  })
  const shell = await app.firstWindow()
  await expect(shell.locator('[data-testid="error"]')).toBeVisible()
  await expect(shell.locator('[data-testid="error-stage"]')).toContainText('数据库迁移')
  await expect(shell.locator('[data-testid="error-message"]')).toContainText('模拟')
  const logPath = await shell.locator('[data-testid="error-log-path"]').textContent()
  expect(logPath).toContain('main.log')
  await expect(shell.locator('[data-testid="error-log-tail"]')).toContainText('阶段开始：migrate')

  const mainPromise = app.waitForEvent('window', {
    predicate: (page) => page.url().startsWith('http://127.0.0.1:'),
  })
  await shell.locator('[data-testid="retry"]').click()
  const main = await mainPromise
  await expect(main.locator('[data-testid="app-ready"]')).toHaveText('CampusFlow 桌面版已就绪')

  const apiPid = readPid(userData, 'api')
  const workerPid = readPid(userData, 'worker')
  // 第二次退出走 SIGTERM，覆盖信号触发的优雅回收路径。
  await quitWithSignal(app, 'SIGTERM')
  await expect.poll(() => isAlive(apiPid), { timeout: 5_000 }).toBe(false)
  await expect.poll(() => isAlive(workerPid), { timeout: 5_000 }).toBe(false)
})
