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
    const mainLog = path.join(logDir, 'main.log')
    const stderr: string[] = []
    app.process().stderr?.on('data', (chunk) => stderr.push(String(chunk)))
    try {
      await waitForLog(mainLog, '全部服务已就绪')
      await waitForLog(mainLog, '自动登录成功')
    } catch (error) {
      throw new Error(`${(error as Error).message}\n--- app stderr ---\n${stderr.join('').slice(-4000)}`)
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
  } finally {
    if (app) {
      try {
        await app.close()
      } catch {
        // 已经退出的应用再次关闭会抛错，忽略即可。
      }
    }
    // Windows 上 PostgreSQL 句柄释放略滞后，清理时重试避免 EBUSY 掩盖真实结果。
    rmSync(root, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 })
  }
})
