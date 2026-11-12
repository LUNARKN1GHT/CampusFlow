import { expect, test } from '@playwright/test'

test('课程路径改为从设置进入工具箱，再进入课程路径页', async ({ page }) => {
  await page.goto('/')
  await page.getByLabel('用户名').fill('student')
  await page.getByLabel('密码').fill('campusflow-dev')
  await page.getByLabel('密码').press('Enter')

  // 课程路径不再占用左侧主导航，也没有新增工具箱主导航项。
  const nav = page.getByRole('navigation', { name: '主要导航' })
  await expect(nav.getByRole('link', { name: '总览', exact: true })).toBeVisible()
  await expect(nav.getByRole('link', { name: '课程路径', exact: true })).toHaveCount(0)
  await expect(nav.getByRole('link', { name: '工具箱', exact: true })).toHaveCount(0)

  // 设置页提供工具箱入口。
  await nav.getByRole('link', { name: '设置', exact: true }).click()
  await expect(page.getByRole('heading', { name: '设置', level: 1 })).toBeVisible()
  await expect(page.getByRole('heading', { name: '工具箱' })).toBeVisible()
  await page.getByRole('link', { name: '进入工具箱', exact: true }).click()

  // 工具箱列出课程路径卡片，点击后进入原页面。
  await expect(page).toHaveURL(/\/tools$/)
  await expect(page.getByRole('heading', { name: '工具箱', level: 1 })).toBeVisible()
  await page.getByRole('link', { name: /培养方案、课程关系与学期试排/ }).click()
  await expect(page).toHaveURL(/\/course-paths$/)
  await expect(page.getByRole('heading', { name: '课程路径', level: 1 })).toBeVisible()

  // 工具箱可以返回设置。
  await page.goto('/tools')
  await page.getByRole('link', { name: '返回设置', exact: true }).click()
  await expect(page).toHaveURL(/\/settings$/)
})
