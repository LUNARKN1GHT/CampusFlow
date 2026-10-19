import { expect, test } from '@playwright/test'

test('界面主题可切换、跨刷新保持并覆盖业务页面', async ({ page }) => {
  await page.goto('/')
  await page.getByLabel('用户名').fill('student')
  await page.getByLabel('密码').fill('campusflow-dev')
  await page.getByLabel('密码').press('Enter')

  const root = page.locator('html')

  await page.getByRole('link', { name: '设置' }).click()
  await expect(page.getByRole('heading', { name: '界面主题' })).toBeVisible()
  // 默认保持原有浅色外观。
  await expect(root).toHaveAttribute('data-theme', 'light')

  // 切换立即生效。
  await page.getByRole('button', { name: '深色', exact: true }).click()
  await expect(root).toHaveAttribute('data-theme', 'dark')

  // 刷新后保持，且业务页面同样生效。
  await page.reload()
  await expect(root).toHaveAttribute('data-theme', 'dark')
  await page.getByRole('link', { name: '课程', exact: true }).click()
  await expect(root).toHaveAttribute('data-theme', 'dark')

  // 跟随系统：随操作系统深浅色自动切换。
  await page.getByRole('link', { name: '设置' }).click()
  await page.getByRole('button', { name: '跟随系统', exact: true }).click()
  await page.emulateMedia({ colorScheme: 'dark' })
  await expect(root).toHaveAttribute('data-theme', 'dark')
  await page.emulateMedia({ colorScheme: 'light' })
  await expect(root).toHaveAttribute('data-theme', 'light')

  // 显式浅色恢复默认。
  await page.getByRole('button', { name: '浅色', exact: true }).click()
  await expect(root).toHaveAttribute('data-theme', 'light')
})
