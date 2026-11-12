import { expect, test } from '@playwright/test'

test.use({ viewport: { width: 390, height: 844 } })

test('窄屏下导航完整可见且页面不横向溢出', async ({ page }) => {
  const pageErrors: string[] = []
  page.on('pageerror', (error) => pageErrors.push(error.message))

  await page.goto('/')
  await page.getByLabel('用户名').fill('student')
  await page.getByLabel('密码').fill('campusflow-dev')
  await page.getByLabel('密码').press('Enter')
  await expect(page.getByRole('heading', { name: /今天先做什么/ })).toBeVisible()

  // 顶部导航在最窄宽度下改为第二行并横向滚动，不能把页面撑宽。
  await expect(page.locator('.app-header')).toBeVisible()
  const nav = page.getByRole('navigation', { name: '主要导航' })
  await expect(nav).toBeVisible()
  expect(await nav.evaluate((el) => getComputedStyle(el).flexWrap)).toBe('nowrap')
  expect(await nav.evaluate((el) => getComputedStyle(el).flexDirection)).toBe('row')

  for (const label of ['总览', '课程', '资料库', '任务', '设置']) {
    await expect(page.getByRole('link', { name: label, exact: true })).toBeVisible()
  }
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1),
  ).toBeTruthy()

  await nav.getByRole('link', { name: '学习计划' }).click()
  await expect(page.getByRole('heading', { name: '学习计划' })).toBeVisible()
  await expect(page.getByRole('button', { name: '生成草案' })).toBeVisible()
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1),
  ).toBeTruthy()
  expect(pageErrors).toEqual([])
})
