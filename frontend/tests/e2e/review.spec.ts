import { expect, test } from '@playwright/test'

test('待核对页面提供筛选与列表控制', async ({ page }) => {
  await page.goto('/')
  await expect(page).toHaveURL(/\/login/)
  await page.getByLabel('用户名').fill('student')
  await page.getByLabel('密码').fill('campusflow-dev')
  await page.getByLabel('密码').press('Enter')
  await expect(page.getByRole('heading', { name: '让学习安排，' })).toBeVisible()

  await page.getByRole('link', { name: '待核对' }).click()
  await expect(page).toHaveURL(/\/review/)
  await expect(page.getByRole('heading', { name: '待核对' })).toBeVisible()

  // 数据库可能为空：只断言空状态或候选行二者之一，不依赖具体种子数据。
  await expect(page.locator('.compact-empty, .candidate-row').first()).toBeVisible()

  const statusSelect = page.locator('.filter-bar').getByLabel('核对状态')
  await expect(statusSelect).toHaveValue('pending')
  await expect(statusSelect.locator('option')).toHaveCount(5)
  await expect(statusSelect.locator('option')).toHaveText(['全部状态', '待核对', '已确认', '已忽略', '已失效'])

  const kindSelect = page.locator('.filter-bar').getByLabel('事项类型')
  await expect(kindSelect.locator('option')).toHaveText(['全部类型', '任务', '日程', '规则'])
  await kindSelect.selectOption('task')
  await expect(kindSelect).toHaveValue('task')

  await expect(page.locator('.filter-bar').getByLabel('来源资料')).toBeVisible()
  await expect(page.locator('.filter-bar').getByLabel('缺失原因')).toBeVisible()
  await expect(page.locator('.count-badge')).toContainText('项')
})
