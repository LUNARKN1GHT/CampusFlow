import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'

const navLabels = ['总览', '课程', '资料库', '任务', '学习计划', '提醒', '导出', '设置']

async function signIn(page: Page) {
  await page.goto('/')
  await page.getByLabel('用户名').fill('student')
  await page.getByLabel('密码').fill('campusflow-dev')
  await page.getByLabel('密码').press('Enter')
  await expect(page.getByRole('heading', { name: /今天先做什么/ })).toBeVisible()
}

// 320px 与 375px 都要能看见并到达全部入口：导航换行而不是隐藏滚动条，账号与主题操作允许换行。
for (const width of [375, 320]) {
  test(`${width}px 下全部导航入口可见且顶栏不横向溢出`, async ({ page }) => {
    await page.setViewportSize({ width, height: 844 })
    const pageErrors: string[] = []
    page.on('pageerror', (error) => pageErrors.push(error.message))

    await signIn(page)

    const nav = page.getByRole('navigation', { name: '主要导航' })
    await expect(nav).toBeVisible()
    expect(await nav.evaluate((el) => getComputedStyle(el).flexWrap)).toBe('wrap')
    expect(await nav.evaluate((el) => getComputedStyle(el).flexDirection)).toBe('row')

    for (const label of navLabels) {
      await expect(nav.getByRole('link', { name: label, exact: true })).toBeVisible()
    }

    // 顶栏自身不能横向溢出，主题切换、用户名与退出都要留在视口内。
    expect(await page.locator('.app-header').evaluate((el) => el.scrollWidth <= el.clientWidth + 1)).toBeTruthy()
    const actions = page.locator('.header-actions')
    const actionsBox = await actions.boundingBox()
    expect(actionsBox).not.toBeNull()
    expect(actionsBox!.x + actionsBox!.width).toBeLessThanOrEqual(width + 1)
    expect(await actions.evaluate((el) => el.scrollWidth <= el.clientWidth + 1)).toBeTruthy()

    // 375px 下沿用整页不横向溢出的既有断言。
    if (width >= 375) {
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBeTruthy()
    }

    // 末尾入口（设置）与计划入口仍然可点击并正常渲染。
    await nav.getByRole('link', { name: '学习计划', exact: true }).click()
    await expect(page.getByRole('heading', { name: '学习计划' })).toBeVisible()
    await expect(page.getByRole('button', { name: '生成草案' })).toBeVisible()
    await nav.getByRole('link', { name: '设置', exact: true }).click()
    await expect(page.getByRole('heading', { name: '设置', exact: true })).toBeVisible()
    expect(pageErrors).toEqual([])
  })
}
