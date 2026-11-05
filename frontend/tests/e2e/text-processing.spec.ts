import { expect, test } from '@playwright/test'

test('文本默认提交处理，可仅存原文并稍后继续处理', async ({ page }) => {
  const requests: Array<{ process_ai: boolean }> = []
  const materials: Array<Record<string, unknown>> = []
  const processing = new Set<number>()
  await page.route('**/api/v1/**', async (route) => {
    const url = new URL(route.request().url())
    const path = url.pathname.replace('/api/v1', '')
    let body: unknown = []
    if (path === '/auth/session') body = { username: 'synthetic-test' }
    if (path === '/materials') body = materials
    if (path === '/materials/text') {
      const payload = route.request().postDataJSON()
      requests.push(payload)
      const id = materials.length + 1
      const material = { id, title: payload.title, status: 'done', source_type: 'text', imported_at: '2026-11-04T08:00:00Z', archived: false, course_id: null, semester_id: null, class_name: null, published_at: null, publisher: null }
      materials.push(material)
      if (payload.process_ai) processing.add(id)
      body = { material, chunks: [{ id }], job_ids: payload.process_ai ? [id * 10, id * 10 + 1] : [] }
    }
    if (path === '/jobs') {
      const id = Number(url.searchParams.get('material_id'))
      body = processing.has(id) ? ['extract', 'embed'].map((stage, index) => ({
        id: id * 10 + index, version_id: id, material_id: id, stage, status: 'pending', is_terminal: false, retryable: false,
      })) : []
    }
    const versions = path.match(/^\/materials\/(\d+)\/versions$/)
    if (versions) body = [{ id: Number(versions[1]), version_no: 1 }]
    if (path.endsWith('/index-progress')) body = { manifest_id: null, summary: null }
    const process = path.match(/^\/materials\/(\d+)\/process$/)
    if (process) { processing.add(Number(process[1])); body = { job_ids: [] } }
    await route.fulfill({ json: body })
  })
  await page.goto('/library')
  await page.getByRole('button', { name: '导入资料', exact: true }).click()
  await page.getByLabel('标题', { exact: true }).fill('合成自动整理')
  await page.getByLabel('正文', { exact: true }).fill('合成通知：周五提交实验报告。')
  await page.getByRole('button', { name: '导入文本', exact: true }).click()
  await expect(page.locator('.material-row').filter({ hasText: '合成自动整理' })).toContainText('资料问答：准备中')
  await page.getByLabel('标题', { exact: true }).fill('合成只存原文')
  await page.getByLabel('正文', { exact: true }).fill('合成通知：周五交作业。')
  await page.getByLabel('导入后整理事项并准备问答（会调用已配置的模型，可能产生费用）').uncheck()
  await page.getByRole('button', { name: '导入文本', exact: true }).click()
  const saved = page.locator('.material-row').filter({ hasText: '合成只存原文' })
  await expect(saved).toContainText('资料问答：未开始')
  expect(requests.map((item) => item.process_ai)).toEqual([true, false])
  await saved.getByRole('button', { name: '继续整理与问答准备' }).click()
  await expect(saved).toContainText('事项整理：等待整理')
  await expect(saved).toContainText('资料问答：准备中')
})
