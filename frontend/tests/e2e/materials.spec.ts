import { execFileSync } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { expect, test } from '@playwright/test'

const root = path.resolve('..')
const python = path.join(root, 'backend/.venv/bin/python')
const helper = path.join(root, 'scripts/m2_browser_fixtures.py')
const enabled = !!process.env.CAMPUSFLOW_M2_E2E
let fixtures: string

function worker(mode: 'done' | 'partial' | 'failed') {
  execFileSync(python, [helper, 'worker', '--mode', mode], {
    cwd: path.join(root, 'backend'), env: process.env, timeout: 60_000, stdio: 'pipe',
  })
}

test.describe('M2 资料库真实队列闭环', () => {
  test.skip(!enabled, '需显式配置隔离数据库、Redis 和存储并启用 CAMPUSFLOW_M2_E2E')
  test.beforeAll(() => {
    fixtures = mkdtempSync(path.join(tmpdir(), 'campusflow-m2-'))
    execFileSync(python, [helper, 'fixtures', '--directory', fixtures])
  })
  test.afterAll(() => { if (fixtures) rmSync(fixtures, { recursive: true, force: true }) })

  test('文本、PDF、图片、部分失败恢复、归档和确认删除', async ({ page, browser }) => {
    test.setTimeout(120_000)
    await page.goto('/login')
    await page.getByLabel('用户名').fill('student')
    await page.getByLabel('密码').fill('campusflow-dev')
    await page.getByLabel('密码').press('Enter')
    await page.getByRole('link', { name: '资料库' }).click()
    await page.getByRole('button', { name: '导入资料', exact: true }).click()
    const title = `M2 text ${Date.now()}`
    await page.getByLabel('标题', { exact: true }).fill(title)
    await page.getByLabel('正文', { exact: true }).fill('课程通知第一段\n\n课程通知第二段\n\n课程通知第一段')
    await page.getByRole('button', { name: '导入文本', exact: true }).click()
    const textRow = page.locator('.material-row').filter({ hasText: title })
    await expect(textRow.locator('[data-status]')).toHaveText('完成')
    await textRow.getByRole('button', { name: '查看原文' }).click()
    await page.locator('.chunk-item').filter({ hasText: '第 2 段' }).click()
    await expect(page.locator('.text-source .highlight')).toHaveText('课程通知第二段')
    await page.locator('.chunk-item').filter({ hasText: '第 3 段' }).click()
    await expect(page.locator('.text-source .highlight')).toHaveText('课程通知第一段')
    expect(await page.locator('.text-source .highlight').evaluate((mark) => mark.previousSibling?.textContent)).toContain('课程通知第二段')
    await page.locator('.source-panel').getByRole('button', { name: '关闭' }).click()

    await page.getByRole('button', { name: '上传 PDF / 图片' }).click()
    await page.getByLabel('选择文件').setInputFiles(path.join(fixtures, 'm2-text.pdf'))
    await page.getByRole('button', { name: '上传并处理' }).click()
    const pdfRow = page.locator('.material-row').filter({ hasText: 'm2-text.pdf' })
    await expect(pdfRow).toBeVisible()
    worker('done')
    await page.getByRole('button', { name: '刷新', exact: true }).click()
    await expect(pdfRow.locator('[data-status]')).toHaveText('完成')
    await pdfRow.getByRole('button', { name: '查看原文' }).click()
    await page.locator('.chunk-item').filter({ hasText: '第 2 页' }).click()
    await expect(page.locator('.pdf-stage iframe')).toHaveAttribute('src', /#page=2/)
    await page.locator('.source-panel').getByRole('button', { name: '关闭' }).click()
    await page.getByLabel('选择文件').setInputFiles(path.join(fixtures, 'm2-text.pdf'))
    await page.getByRole('button', { name: '上传并处理' }).click()
    await expect(page.getByText('与已有资料相同 · 未重复建档')).toBeVisible()
    await expect(pdfRow).toHaveCount(1)

    await page.getByLabel('选择文件').setInputFiles(path.join(fixtures, 'm2-image.png'))
    await page.getByRole('button', { name: '上传并处理' }).click()
    const imageRow = page.locator('.material-row').filter({ hasText: 'm2-image.png' })
    await expect(imageRow).toBeVisible()
    worker('partial')
    await page.getByRole('button', { name: '刷新', exact: true }).click()
    await expect(imageRow.locator('[data-status]')).toHaveText('部分成功')
    await expect(imageRow).toContainText('下半区暂时未识别')
    // 全新浏览器上下文没有本地作业映射，仍须从服务端恢复范围和重试入口。
    const context = await browser.newContext()
    const fresh = await context.newPage()
    await fresh.goto('/login')
    await fresh.getByLabel('用户名').fill('student')
    await fresh.getByLabel('密码').fill('campusflow-dev')
    await fresh.getByLabel('密码').press('Enter')
    await fresh.getByRole('link', { name: '资料库' }).click()
    const recovered = fresh.locator('.material-row').filter({ hasText: 'm2-image.png' })
    await expect(recovered).toContainText('下半区暂时未识别')
    await recovered.getByRole('button', { name: '重试处理' }).click()
    worker('failed')
    await fresh.getByRole('button', { name: '刷新', exact: true }).click()
    await expect(recovered.locator('[data-status]')).toHaveText('失败')
    await expect(recovered).toContainText('视觉服务暂时不可用')
    await recovered.getByRole('button', { name: '重试处理' }).click()
    worker('done')
    await fresh.getByRole('button', { name: '刷新', exact: true }).click()
    await expect(recovered.locator('[data-status]')).toHaveText('完成')
    await recovered.getByRole('button', { name: '查看原文' }).click()
    await expect(fresh.locator('.image-stage img')).toBeVisible()
    await expect(fresh.locator('.bbox-highlight')).toHaveAttribute('style', /left: 10%; top: 10%; width: 80%; height: 20%/)
    await fresh.screenshot({ path: test.info().outputPath('m2-image-source.png'), fullPage: true })
    await fresh.locator('.source-panel').getByRole('button', { name: '关闭' }).click()
    await context.close()

    await textRow.getByRole('button', { name: '归档 / 删除' }).click()
    await page.getByRole('button', { name: '归档资料', exact: true }).click()
    await expect(textRow).toHaveCount(0)
    await page.getByLabel('包含已归档').check()
    await expect(textRow).toBeVisible()
    await page.getByRole('button', { name: '恢复已归档资料' }).click()
    await expect(textRow.getByText('已归档', { exact: true })).toHaveCount(0)
    await page.getByRole('button', { name: '预览删除影响' }).click()
    await expect(page.getByText('个片段将失效')).toBeVisible()
    const materials = await (await page.request.get('/api/v1/materials')).json() as { id: number; title: string }[]
    const materialId = materials.find((item) => item.title === title)!.id
    const task = JSON.parse(execFileSync(python, [helper, 'link-task', '--material-id', String(materialId)], {
      env: process.env, encoding: 'utf8',
    })) as { id: number; title: string }
    // 预览后出现新关联任务，旧确认必须由真实后端拒绝，重新预览才可执行。
    await page.getByRole('button', { name: '确认删除以上范围' }).click()
    await expect(page.getByText(/删除未完成：影响内容已变化/)).toBeVisible()
    await expect(textRow).toBeVisible()
    await page.getByRole('button', { name: '预览删除影响' }).click()
    await page.getByRole('button', { name: '确认删除以上范围' }).click()
    await expect(page.getByText(/删除完成：原文件与派生内容已移除/)).toBeVisible()
    await expect(textRow).toHaveCount(0)
    const tasks = await (await page.request.get('/api/v1/tasks')).json() as { id: number; source_state: string }[]
    expect(tasks.find((item) => item.id === task.id)?.source_state).toBe('invalidated')
    expect((await page.request.get(`/api/v1/materials/${materialId}/versions/1/file`)).status()).toBe(404)
    await page.getByRole('link', { name: '任务', exact: true }).click()
    await expect(page.locator('.task-row').filter({ hasText: task.title })).toContainText('来源资料')
    await expect(page.locator('.task-row').filter({ hasText: task.title })).toContainText('引用不可用')
  })
})
