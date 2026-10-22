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

test('深色模式次要文本保持可读对比度', async ({ page }) => {
  await page.goto('/')
  await page.getByLabel('用户名').fill('student')
  await page.getByLabel('密码').fill('campusflow-dev')
  await page.getByLabel('密码').press('Enter')
  await page.getByRole('link', { name: '设置' }).click()
  await page.getByRole('button', { name: '深色', exact: true }).click()
  await page.getByRole('link', { name: '任务', exact: true }).click()

  const placeholder = page.locator('.day-empty').first()
  await expect(placeholder).toBeVisible()
  const ratio = await placeholder.evaluate((el) => {
    const parse = (value: string) => {
      const [r, g, b, a = 1] = value.match(/[\d.]+/g)!.map(Number)
      return { r, g, b, a }
    }
    const luminance = ({ r, g, b }: { r: number; g: number; b: number }) => {
      const channel = (v: number) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4 }
      return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)
    }
    const foreground = parse(getComputedStyle(el).color)
    const background = parse(getComputedStyle(el.parentElement!).backgroundColor)
    const [light, dark] = [luminance(foreground), luminance(background)].sort((x, y) => y - x)
    return (light + 0.05) / (dark + 0.05)
  })
  expect(ratio).toBeGreaterThanOrEqual(4.5)
})
