import { existsSync, mkdtempSync, readFileSync, rmSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'

import { expect, test, type ElectronApplication } from '@playwright/test'
import { _electron as electron } from 'playwright'

/**
 * 打包应用的数据维护联调：真实 sidecar + 内置 PostgreSQL，经菜单触发导出、升级检查与导入。
 * 依赖本机已构建的产物，默认跳过；本地执行：
 *
 *   CAMPUSFLOW_PACKAGED_APP=release/mac-arm64/CampusFlow.app/Contents/MacOS/CampusFlow \
 *   CAMPUSFLOW_SIDECAR_BIN=../dist/campusflow-sidecar/campusflow-sidecar \
 *   CAMPUSFLOW_PG_BIN=../vendor/pgsql/bin \
 *   npx playwright test tests/packaged-maintenance.spec.ts
 */
const APP = process.env.CAMPUSFLOW_PACKAGED_APP
const SIDECAR = process.env.CAMPUSFLOW_SIDECAR_BIN
const PG_BIN = process.env.CAMPUSFLOW_PG_BIN
const ready = Boolean(APP && SIDECAR && PG_BIN && existsSync(APP) && existsSync(SIDECAR))

interface DialogMessage {
  title?: string
  message?: string
  detail?: string
  buttons?: string[]
}

async function waitForLog(file: string, needle: string, timeoutMs = 150_000): Promise<void> {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    if (existsSync(file)) {
      const content = readFileSync(file, 'utf8')
      if (content.includes(needle)) return
    }
    await new Promise((resolve) => setTimeout(resolve, 250))
  }
  const tail = existsSync(file) ? readFileSync(file, 'utf8').slice(-4000) : '(日志文件不存在)'
  throw new Error(`等待日志超时：${needle}\n--- ${file} ---\n${tail}`)
}

async function waitForExit(app: ElectronApplication, timeoutMs = 30_000): Promise<void> {
  const process = app.process()
  if (process.exitCode !== null) return
  await new Promise<void>((resolve) => {
    const timer = setTimeout(resolve, timeoutMs)
    process.once('exit', () => {
      clearTimeout(timer)
      resolve()
    })
  })
}

async function messages(app: ElectronApplication): Promise<DialogMessage[]> {
  return app.evaluate(() => ((globalThis as Record<string, unknown>).__cfMessages ?? []) as [])
}

async function clickDataMenu(app: ElectronApplication, prefix: string): Promise<void> {
  const clicked = await app.evaluate(({ Menu }, labelPrefix) => {
    const menu = Menu.getApplicationMenu()
    const data = menu?.items.find((item) => item.label === '数据')
    const item = data?.submenu?.items.find((entry) => entry.label.startsWith(labelPrefix))
    if (!item) return false
    item.click()
    return true
  }, prefix)
  if (!clicked) throw new Error(`菜单项不存在：${prefix}`)
}

test('打包应用经菜单导出、检查升级并导入归档', async () => {
  test.skip(!ready, '需要 CAMPUSFLOW_PACKAGED_APP / CAMPUSFLOW_SIDECAR_BIN / CAMPUSFLOW_PG_BIN')
  test.setTimeout(300_000)

  const root = mkdtempSync(path.join(os.tmpdir(), 'campusflow-p010-e2e-'))
  const userData = path.join(root, 'user')
  const dataDir = path.join(root, 'data')
  const logDir = path.join(root, 'logs')
  const archive = path.join(root, 'roundtrip.campusflow')
  let app: ElectronApplication | null = null

  try {
    app = await electron.launch({
      executablePath: APP,
      timeout: 120_000,
      env: {
        ...process.env,
        CAMPUSFLOW_ELECTRON_USER_DATA: userData,
        CAMPUSFLOW_DATA_DIR: dataDir,
        CAMPUSFLOW_LOG_DIR: logDir,
        CAMPUSFLOW_API_PORT: '8141',
        CAMPUSFLOW_PG_PORT: '55443',
        CAMPUSFLOW_PG_BIN: PG_BIN!,
        CAMPUSFLOW_SIDECAR_BIN: SIDECAR!,
      },
    })
    // 主窗口在服务就绪后才创建；先注册监听，避免只等到日志却错过窗口。
    const mainWindowPromise = app.waitForEvent('window', {
      predicate: (page) => page.url().startsWith('http://127.0.0.1:'),
      timeout: 180_000,
    })
    const mainLog = path.join(logDir, 'main.log')
    const stderr: string[] = []
    app.process().stderr?.on('data', (chunk) => stderr.push(String(chunk)))
    try {
      await waitForLog(mainLog, '全部服务已就绪')
      await waitForLog(mainLog, '自动登录成功')
    } catch (error) {
      throw new Error(`${(error as Error).message}\n--- app stderr ---\n${stderr.join('').slice(-4000)}`)
    }
    // 自动登录后应进入真实 SPA，而不是停留在登录页。
    const mainWindow = await mainWindowPromise
    await expect(mainWindow).toHaveTitle(/CampusFlow/)
    expect(mainWindow.url()).not.toContain('/login')
    await expect(mainWindow.locator('body')).toContainText('总览', { timeout: 30_000 })

    // 未显式指定后端时，Windows 必须使用 DPAPI；回退到文件说明凭据保护失效。
    if (process.platform === 'win32' && !process.env.CAMPUSFLOW_SECRET_BACKEND) {
      expect(readFileSync(mainLog, 'utf8')).toContain('凭据后端 windows-dpapi')
    }

    // 原生对话框在自动化里不可操作：替换为脚本结果，其余流程与真实菜单一致。
    await app.evaluate(({ dialog, app: electronApp }, config) => {
      const scope = globalThis as Record<string, unknown>
      const recorded: unknown[] = []
      scope.__cfMessages = recorded
      const record = (args: unknown[]): void => {
        recorded.push(args[args.length - 1])
      }
      dialog.showSaveDialog = (async (...args: unknown[]) => {
        record(args)
        return { canceled: false, filePath: config.archive }
      }) as unknown as typeof dialog.showSaveDialog
      dialog.showOpenDialog = (async (...args: unknown[]) => {
        record(args)
        return { canceled: false, filePaths: [config.archive] }
      }) as unknown as typeof dialog.showOpenDialog
      dialog.showMessageBox = (async (...args: unknown[]) => {
        record(args)
        return { response: 1 }
      }) as unknown as typeof dialog.showMessageBox
      const keepOpen = (): void => {}
      scope.__cfExit = electronApp.exit.bind(electronApp)
      electronApp.relaunch = keepOpen
      electronApp.exit = keepOpen as typeof electronApp.exit
    }, { archive })

    await clickDataMenu(app, '导出数据归档')
    const exportDeadline = Date.now() + 120_000
    while (!existsSync(archive) && Date.now() < exportDeadline) {
      await new Promise((resolve) => setTimeout(resolve, 500))
    }
    expect(existsSync(archive)).toBe(true)
    await expect
      .poll(async () => (await messages(app!)).some((item) => item.title === '导出完成'), {
        timeout: 30_000,
      })
      .toBe(true)
    const exported = (await messages(app)).find((item) => item.title === '导出完成')
    expect(exported?.detail).toContain('SHA-256')
    expect(exported?.detail).toContain('已排除凭据')

    await clickDataMenu(app, '检查数据库升级')
    await expect
      .poll(async () => (await messages(app!)).some((item) => item.title === '数据库已是最新'), {
        timeout: 30_000,
      })
      .toBe(true)

    const before = (await messages(app)).length
    await clickDataMenu(app, '从归档导入')
    await expect
      .poll(async () => {
        const list = await messages(app!)
        return list.slice(before).some((item) => item.title === '导入完成')
      }, { timeout: 120_000 })
      .toBe(true)
    const list = await messages(app)
    const confirm = list.find((item) => item.title === '导入 CampusFlow 数据')
    expect(confirm?.detail).toContain('导入会整体替换本机现有数据')
    const imported = list.find((item) => item.title === '导入完成')
    expect(imported?.detail).toContain('已恢复条目')

    // 恢复真实退出函数，按正常路径回收服务与 PostgreSQL。
    await app.evaluate(() => {
      const scope = globalThis as Record<string, unknown>
      ;(scope.__cfExit as (code: number) => void)(0)
    })
    await waitForExit(app)
  } finally {
    if (app) {
      try {
        await app.close()
      } catch {
        // 已经退出的应用再次关闭会抛错，忽略即可。
      }
    }
    try {
      // Windows 上 PostgreSQL 句柄释放略滞后，清理重试更久；失败不改变用例结论。
      rmSync(root, { recursive: true, force: true, maxRetries: 40, retryDelay: 250 })
    } catch (error) {
      console.warn(`临时目录清理失败：${(error as Error).message}`)
    }
  }
})

test('打包应用可导航到所有已实现页面', async () => {
  test.skip(!ready, '需要 CAMPUSFLOW_PACKAGED_APP / CAMPUSFLOW_SIDECAR_BIN / CAMPUSFLOW_PG_BIN')
  test.setTimeout(300_000)

  const root = mkdtempSync(path.join(os.tmpdir(), 'campusflow-nav-e2e-'))
  const userData = path.join(root, 'user')
  const dataDir = path.join(root, 'data')
  const logDir = path.join(root, 'logs')
  let app: ElectronApplication | null = null

  try {
    app = await electron.launch({
      executablePath: APP,
      timeout: 120_000,
      env: {
        ...process.env,
        CAMPUSFLOW_ELECTRON_USER_DATA: userData,
        CAMPUSFLOW_DATA_DIR: dataDir,
        CAMPUSFLOW_LOG_DIR: logDir,
        CAMPUSFLOW_API_PORT: '8142',
        CAMPUSFLOW_PG_PORT: '55444',
        CAMPUSFLOW_PG_BIN: PG_BIN!,
        CAMPUSFLOW_SIDECAR_BIN: SIDECAR!,
      },
    })
    const mainWindowPromise = app.waitForEvent('window', {
      predicate: (page) => page.url().startsWith('http://127.0.0.1:'),
      timeout: 180_000,
    })
    await waitForLog(path.join(logDir, 'main.log'), '自动登录成功')
    const main = await mainWindowPromise
    await expect(main.getByRole('link', { name: '总览', exact: true })).toBeVisible({ timeout: 30_000 })

    const routes: Array<[string, string]> = [
      ['课程', '/courses'],
      ['课程路径', '/course-paths'],
      ['资料库', '/library'],
      ['待核对', '/review'],
      ['版本对照', '/review/changes'],
      ['智能问答', '/qa'],
      ['任务', '/tasks'],
      ['学习计划', '/plan'],
      ['提醒', '/reminders'],
      ['导出', '/export'],
      ['外部日历', '/calendar'],
      ['设置', '/settings'],
      ['总览', '/'],
    ]
    for (const [label, route] of routes) {
      await main.getByRole('link', { name: label, exact: true }).click()
      await expect.poll(() => new URL(main.url()).pathname, { timeout: 15_000 }).toBe(route)
      await expect(main.locator('h1, h2').first()).toBeVisible()
    }
  } finally {
    if (app) {
      try {
        const closed = app.waitForEvent('close', { timeout: 10_000 })
        await app.evaluate(({ app: electronApp }) => electronApp.quit())
        await closed
      } catch {
        app.process().kill('SIGKILL')
      }
    }
    try {
      rmSync(root, { recursive: true, force: true, maxRetries: 40, retryDelay: 250 })
    } catch (error) {
      console.warn(`临时目录清理失败：${(error as Error).message}`)
    }
  }
})
