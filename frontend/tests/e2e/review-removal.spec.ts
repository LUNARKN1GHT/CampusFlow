import { expect, test } from '@playwright/test'

import { login } from './support'

test('待核对与版本对照页面已移除并回退到总览', async ({ page }) => {
  await login(page)

  const nav = page.getByRole('navigation', { name: '主要导航' })
  await expect(nav.getByRole('link', { name: '待核对', exact: true })).toHaveCount(0)
  await expect(nav.getByRole('link', { name: '版本对照', exact: true })).toHaveCount(0)

  await page.goto('/review')
  await expect(page).toHaveURL((url) => url.pathname === '/')
  await expect(page.getByRole('heading', { name: /今天先做什么/ })).toBeVisible()

  await page.goto('/review/changes')
  await expect(page).toHaveURL((url) => url.pathname === '/')
  await expect(page.getByRole('heading', { name: /今天先做什么/ })).toBeVisible()
  await expect(page.getByRole('heading', { name: '待核对', level: 1 })).toHaveCount(0)
  await expect(page.getByRole('heading', { name: '版本对照', level: 1 })).toHaveCount(0)
})
