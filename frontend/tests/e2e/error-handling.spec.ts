import { expect, test } from '@playwright/test'

import { e2eAccount, login } from './support'

// 错误态回归：会话失效与权限不足必须让用户看到明确结果，而不是停在看似正常的页面上。
// 全部通过 page.route 模拟后端响应，不实际写入数据。

test('业务接口返回 401 时跳转登录页并在重新登录后回到原页面', async ({ page }) => {
  await login(page)

  // 只拦截任务列表：任意受保护接口拿到 401 都应触发统一的会话失效处理。
  await page.route('**/api/v1/tasks*', (route) =>
    route.fulfill({
      status: 401,
      contentType: 'application/json',
      body: JSON.stringify({ detail: '未登录' }),
    }),
  )

  await page.getByRole('navigation', { name: '主要导航' }).getByRole('link', { name: '任务', exact: true }).click()

  // 会话失效后跳到登录页并带回原路径；expired=1 让登录页说明原因。
  await expect(page).toHaveURL(/\/login\?/)
  const expiredUrl = new URL(page.url())
  expect(expiredUrl.pathname).toBe('/login')
  expect(expiredUrl.searchParams.get('expired')).toBe('1')
  expect(expiredUrl.searchParams.get('redirect')).toBe('/tasks')
  await expect(page.getByText('会话已失效，请重新登录。私有页面内容已清除。')).toBeVisible()

  // 解除拦截后重新登录：应回到原来要访问的任务页，而不是总览。
  await page.unroute('**/api/v1/tasks*')
  await page.getByLabel('用户名').fill(e2eAccount.username)
  await page.getByLabel('密码').fill(e2eAccount.password)
  await page.getByRole('button', { name: '登录' }).click()
  await expect(page).toHaveURL(/\/tasks$/)
  await expect(page.getByRole('heading', { name: '任务', level: 1 })).toBeVisible()
})

test('保存工作空间设置返回 403 时显示后端原因且没有成功提示', async ({ page }) => {
  await login(page)
  await page.getByRole('navigation', { name: '主要导航' }).getByRole('link', { name: '设置', exact: true }).click()
  // 等设置读取完成，确保下面的拦截只命中保存请求。
  await expect(page.getByRole('button', { name: '保存设置' })).toBeVisible()

  await page.route('**/api/v1/settings', async (route) => {
    if (route.request().method() !== 'PUT') {
      await route.fallback()
      return
    }
    await route.fulfill({
      status: 403,
      contentType: 'application/json',
      body: JSON.stringify({ detail: '当前会话无权修改该工作空间' }),
    })
  })

  await page.getByRole('button', { name: '保存设置' }).click()

  // 4xx 使用后端 detail 文案；请求被拒绝时不能出现任何“已保存”提示。
  await expect(page.getByText('当前会话无权修改该工作空间')).toBeVisible()
  await expect(page.getByText('学习偏好已保存。')).toHaveCount(0)

  await page.unroute('**/api/v1/settings')
})
