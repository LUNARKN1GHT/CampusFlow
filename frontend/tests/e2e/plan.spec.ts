import { expect, test } from '@playwright/test'

function dateKey(date: Date): string {
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

test('两周计划可生成、接受、锁定并移动，且总览读取当前计划', async ({ page }) => {
  const unique = Date.now().toString().slice(-6)
  const taskName = `计划验收任务 ${unique}`
  const today = new Date()
  const due = new Date(today.getFullYear(), today.getMonth(), today.getDate() + 2)
  const dueDate = dateKey(due)
  const weekday = (due.getDay() + 6) % 7

  const pageErrors: string[] = []
  page.on('pageerror', (error) => pageErrors.push(error.message))

  await page.goto('/')
  await page.getByLabel('用户名').fill('student')
  await page.getByLabel('密码').fill('campusflow-dev')
  await page.getByLabel('密码').press('Enter')
  await expect(page.getByRole('heading', { name: /今天先做什么/ })).toBeVisible()

  await page.getByRole('link', { name: '设置' }).click()
  await page.getByLabel('星期').selectOption(String(weekday))
  await page.getByLabel('开始').fill('18:00')
  await page.getByLabel('结束').fill('22:00')
  await page.getByRole('button', { name: '添加时段' }).click()
  await expect(page.getByText('18:00–22:00')).toBeVisible()

  await page.getByRole('link', { name: '任务', exact: true }).click()
  await page.getByRole('button', { name: '添加任务' }).click()
  await page.getByLabel('任务标题').fill(taskName)
  await page.getByLabel('正式 DDL 日期').fill(dueDate)
  await page.getByLabel('DDL 时刻（可留空）').fill('21:00')
  await page.getByLabel('优先级').selectOption('high')
  await page.getByLabel('原始预计分钟').fill('60')
  await page.getByLabel('当前剩余分钟').fill('60')
  await page.getByRole('button', { name: '保存任务' }).click()
  await expect(page.getByText(taskName)).toBeVisible()

  await page.getByRole('link', { name: '学习计划' }).click()
  await page.getByRole('button', { name: '生成未来两周草案' }).click()
  await expect(page.getByText('待接受草案')).toBeVisible()

  const block = page.locator('.block-row').filter({ hasText: taskName })
  await expect(block).toContainText('60 分钟')
  await expect(block).toContainText('为满足')
  await expect(block).toContainText('提交缓冲')

  await page.getByRole('button', { name: '接受为当前计划' }).click()
  await expect(page.getByText('当前计划')).toBeVisible()

  await block.getByRole('button', { name: '锁定' }).click()
  await expect(block.getByText('已锁定')).toBeVisible()

  await block.getByRole('button', { name: '调整时间' }).click()
  await block.getByLabel('开始').fill(`${dueDate}T21:00`)
  await block.getByLabel('结束').fill(`${dueDate}T21:30`)
  await block.getByRole('button', { name: '保存' }).click()
  await expect(block).toContainText('21:00–21:30')
  await expect(block).toContainText('用户手动移动')

  await page.getByRole('link', { name: '总览', exact: true }).click()
  await expect(page.getByText(taskName)).toBeVisible()
  expect(pageErrors).toEqual([])
})
