import { expect, test } from '@playwright/test'

test('AI 设置保存、清除、刷新和独立检查，不持久化或回显密钥', async ({ page }) => {
  let configured = false
  const writes: unknown[] = []
  const checks: string[] = []
  const publicConfig = () => ({ editable: true, text_provider: 'zhipu', text_model: 'glm-4-flash-250414', vision_model: 'glm-4v-flash', embedding_model: 'embedding-3', embedding_dimensions: 1024, zhipu_configured: configured, deepseek_configured: false, capabilities: { text: configured, vision: configured, embedding: configured } })
  await page.route('**/api/v1/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    let body: unknown = []
    if (path.endsWith('/auth/session')) body = { username: 'synthetic-test' }
    if (path.endsWith('/settings')) body = { timezone: 'Asia/Shanghai', daily_capacity_minutes: 240, weekly_capacity_minutes: 1000, break_minutes: 15, buffer_minutes: 30 }
    if (path.endsWith('/settings/ai')) {
      if (route.request().method() === 'PUT') {
        const input = route.request().postDataJSON()
        writes.push(input)
        if (input.zhipu_api_key !== null) configured = Boolean(input.zhipu_api_key)
      }
      body = publicConfig()
    }
    if (path.endsWith('/settings/ai/check')) {
      const input = route.request().postDataJSON()
      expect(input.acknowledge_cost).toBe(true)
      checks.push(input.capability)
      body = { ok: input.capability !== 'vision', message: input.capability === 'vision' ? '图片服务拒绝请求' : '合成样本检查通过' }
    }
    await route.fulfill({ json: body })
  })
  await page.goto('/settings')
  const section = page.getByRole('region', { name: 'AI 服务设置' })
  const keyInput = section.getByLabel('智谱密钥（图片、检索及智谱文字共用）')
  await expect(section.getByRole('button', { name: '检查文字生成' })).toBeDisabled()
  await keyInput.fill('synthetic-secret')
  await section.getByRole('button', { name: '保存 AI 设置' }).click()
  await expect(keyInput).toHaveValue('')
  await expect(section).toContainText('已安全保存')
  expect(checks).toEqual([])
  await section.getByLabel('我同意用合成样本检查，了解可能产生费用').check()
  await section.getByRole('button', { name: '检查文字生成' }).click()
  await expect(section).toContainText('通过：合成样本检查通过')
  await section.getByRole('button', { name: '检查图片识别' }).click()
  await expect(section).toContainText('未通过：图片服务拒绝请求')
  expect(checks).toEqual(['text', 'vision'])
  await page.reload()
  await expect(section).toContainText('已配置（尚需检查连接）')
  await expect(keyInput).toHaveValue('')
  expect(await page.evaluate(() => JSON.stringify(localStorage) + JSON.stringify(sessionStorage))).not.toContain('synthetic-secret')
  await section.getByLabel('清除已保存的智谱密钥').check()
  await section.getByRole('button', { name: '保存 AI 设置' }).click()
  await expect(section).toContainText('文字生成：未配置')
  expect(writes).toHaveLength(2)
})
