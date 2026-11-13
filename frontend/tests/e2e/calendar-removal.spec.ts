import { expect, test } from '@playwright/test'

import { login } from './support'

// 外部日历前端页面与入口已移除：/calendar 由 catch-all 回退到总览，导航不再出现该入口。
test('访问 /calendar 回退到总览且不渲染外部日历页面', async ({ page }) => {
  await login(page)

  // 导航不再提供外部日历入口。
  await expect(
    page.getByRole('navigation', { name: '主要导航' }).getByRole('link', { name: '外部日历' }),
  ).toHaveCount(0)

  // 旧地址由未知路径回退到总览，且不出现日历页面标题。
  await page.goto('/calendar')
  await expect(page).toHaveURL((url) => url.pathname === '/')
  await expect(page.getByRole('heading', { name: /今天先做什么/ })).toBeVisible()
  await expect(page.getByRole('heading', { name: '外部日历' })).toHaveCount(0)
})
