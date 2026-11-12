import { existsSync, mkdirSync, readFileSync } from 'node:fs'
import path from 'node:path'
import { expect, test, type ElectronApplication, type Page } from '@playwright/test'
import { _electron as electron } from 'playwright'

const executable = process.env.CAMPUSFLOW_PACKAGED_APP

test('修复版真实桌面：配置持久化、文本处理失败可见、父任务汇总', async () => {
  test.skip(!executable || !existsSync(executable), '需要本地已构建的桌面程序')
  test.setTimeout(300_000)
  const root = path.resolve(__dirname, '../../.scratch', `desktop-fixes-${Date.now()}`)
  mkdirSync(root, { recursive: true })
  console.log(`验收目录：${root}`)
  const env = Object.fromEntries(Object.entries(process.env).filter(([key]) =>
    !key.startsWith('CAMPUSFLOW_') && !['ZHIPU_API_KEY', 'DEEPSEEK_API_KEY'].includes(key)))
  let app: ElectronApplication | null = null
  const runtimeBins: string[] = []
  async function launch(): Promise<Page> {
    app = await electron.launch({
      executablePath: executable, cwd: root, timeout: 120_000,
      env: { ...env, CAMPUSFLOW_ELECTRON_USER_DATA: path.join(root, 'user'),
        CAMPUSFLOW_PG_PORT: '55449', ZHIPU_API_KEY: '', DEEPSEEK_API_KEY: '' },
    })
    await expect.poll(() => app!.windows().some(p => p.url().startsWith('http://127.0.0.1:')),
      { timeout: 150_000 }).toBe(true)
    const page = app.windows().find(p => p.url().startsWith('http://127.0.0.1:'))!
    await expect(page.getByRole('link', { name: '资料库', exact: true })).toBeVisible({ timeout: 30_000 })
    if (process.platform === 'win32' && /[^\x20-\x7e]/.test(executable!)) {
      const log = readFileSync(path.join(root, 'user', 'logs', 'main.log'), 'utf8')
      const bins = [...log.matchAll(/数据库运行文件：([^\r\n]+)/g)]
      const bin = bins.at(-1)?.[1]?.trim()
      expect(bin).toBeTruthy()
      expect(bin).not.toMatch(/[^\x20-\x7e]/)
      expect(existsSync(path.join(bin!, 'initdb.exe'))).toBe(true)
      expect(existsSync(path.join(root, 'user', 'data', 'pgdata', 'PG_VERSION'))).toBe(true)
      expect(existsSync(path.join(bin!, '..', 'pgdata'))).toBe(false)
      runtimeBins.push(bin!)
      if (runtimeBins.length > 1) expect(bin).toBe(runtimeBins[0])
    }
    return page
  }
  async function quit() {
    if (!app) return
    const closed = app.waitForEvent('close', { timeout: 30_000 })
    await app.evaluate(({ app: instance }) => instance.quit())
    await closed
    app = null
  }
  async function api(page: Page, route: string, body?: Record<string, unknown>, method = 'POST') {
    return page.evaluate(async ({ route, body, method }) => {
      const response = await fetch(`/api/v1${route}`, {
        method: body === undefined ? 'GET' : method, credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      })
      if (!response.ok) throw new Error(`验收接口 ${route}: ${response.status}`)
      return response.json()
    }, { route, body, method })
  }
  try {
    let page = await launch()
    const origin = new URL(page.url()).origin
    await page.goto(`${origin}/settings`)
    const settings = page.getByRole('region', { name: 'AI 服务设置' })
    await expect(settings).toContainText('未配置')
    await settings.getByLabel('智谱密钥（图片、检索及智谱文字共用）', { exact: true }).fill('synthetic-desktop-key-no-network')
    await settings.getByRole('button', { name: '保存 AI 设置', exact: true }).click()
    await expect(settings).toContainText('已安全保存')
    await expect(settings.locator('input[type=password]').first()).toHaveValue('')
    const saved = await api(page, '/settings/ai')
    expect(saved.zhipu_configured).toBe(true)
    expect(JSON.stringify(saved)).not.toContain('synthetic-desktop-key')
    await quit()
    page = await launch()
    expect((await api(page, '/settings/ai')).zhipu_configured).toBe(true)
    await page.goto(`${new URL(page.url()).origin}/settings`)
    await page.getByLabel('清除已保存的智谱密钥').check()
    await page.getByRole('button', { name: '保存 AI 设置', exact: true }).click()
    await expect(page.getByRole('region', { name: 'AI 服务设置' })).toContainText('已安全保存')
    expect((await api(page, '/settings/ai')).zhipu_configured).toBe(false)
    await page.screenshot({ path: path.join(root, '01-settings.png'), fullPage: true })

    // 清空密钥后才提交资料：验证真实后台的缺配置路径，不请求外部模型。
    await page.goto(`${new URL(page.url()).origin}/library`)
    await page.getByRole('button', { name: '导入资料', exact: true }).click()
    await page.getByLabel('标题', { exact: true }).fill('合成验收：数据结构作业')
    await page.getByLabel('正文', { exact: true }).fill('数据结构一班：2026年11月20日前提交二叉树实验报告。预计需要60分钟。')
    await expect(page.getByLabel('导入后整理事项并准备问答', { exact: false })).toBeChecked()
    await page.getByRole('button', { name: '导入文本', exact: true }).click()
    const row = page.locator('article.material-row').filter({ hasText: '合成验收：数据结构作业' })
    await expect(row).toContainText('原文读取完成')
    await expect(row).toContainText('资料问答：准备失败', { timeout: 45_000 })
    await expect(row).toContainText('文字模型配置不可用')
    await expect(row.getByRole('link', { name: '打开 AI 服务设置' })).toBeVisible()
    await expect(row.getByRole('button', { name: /重试.*问答|重试.*索引/ })).toBeVisible()
    expect((await api(page, '/tasks')).length).toBe(0)
    expect((await api(page, '/candidates')).length).toBe(0)
    await page.screenshot({ path: path.join(root, '02-processing.png'), fullPage: true })

    const parent = await api(page, '/tasks', { title: '合成验收：实验报告总任务', estimated_minutes: 60 })
    const child = await api(page, '/tasks', { title: '合成验收：阅读资料', estimated_minutes: 20, parent_task_id: parent.id })
    await api(page, '/tasks', { title: '合成验收：撰写报告', estimated_minutes: 40, parent_task_id: parent.id })
    await page.goto(`${new URL(page.url()).origin}/tasks`)
    const parentCard = page.locator(`[data-task-id="${parent.id}"]`)
    await expect(parentCard.locator('strong')).toContainText('60 分钟剩余')
    await page.locator(`[data-task-id="${child.id}"]`).getByRole('button', { name: '进度', exact: true }).click()
    await page.getByLabel('新状态').selectOption('done')
    await page.getByRole('button', { name: '更新进度', exact: true }).click()
    await expect(parentCard.locator('strong')).toContainText('40 分钟剩余')
    await parentCard.getByRole('button', { name: '依赖与子任务' }).click()
    await expect(page.getByRole('region', { name: '依赖与子任务' })).toContainText('剩余 40 分钟')
    await page.reload()
    await expect(parentCard.locator('strong')).toContainText('40 分钟剩余')
    await page.screenshot({ path: path.join(root, '03-task-rollup.png'), fullPage: true })

    // pg_dump 也必须使用英文运行副本；仅将合成数据导出到本轮验收目录。
    const archive = path.join(root, 'synthetic-backup.campusflow')
    await app!.evaluate(({ dialog, Menu }, filePath) => {
      const scope = globalThis as Record<string, unknown>
      scope.__cfExportMessages = []
      dialog.showSaveDialog = (async () => ({ canceled: false, filePath })) as typeof dialog.showSaveDialog
      dialog.showMessageBox = (async (...args: unknown[]) => {
        (scope.__cfExportMessages as unknown[]).push(args.at(-1))
        return { response: 0 }
      }) as typeof dialog.showMessageBox
      const item = Menu.getApplicationMenu()?.items.find(entry => entry.label === '数据')
        ?.submenu?.items.find(entry => entry.label.startsWith('导出数据归档'))
      if (!item) throw new Error('缺少导出数据归档菜单')
      item.click()
    }, archive)
    await expect.poll(async () => app!.evaluate(() =>
      ((globalThis as Record<string, unknown>).__cfExportMessages as { title?: string }[])
        .some(message => message.title === '导出完成')), { timeout: 60_000 }).toBe(true)
    expect(existsSync(archive)).toBe(true)
    expect(readFileSync(path.join(root, 'user', 'logs', 'main.log'), 'utf8')).toContain('凭据后端 windows-dpapi')
  } finally {
    await quit()
  }
})
