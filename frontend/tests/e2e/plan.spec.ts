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

  await page.getByRole('navigation', { name: '主要导航' }).getByRole('link', { name: '学习计划' }).click()
  await page.getByRole('button', { name: '生成草案' }).click()
  await expect(page.getByText('待接受草案')).toBeVisible()

  const block = page.locator('.block-row').filter({ hasText: taskName })
  await expect(block).toContainText('60 分钟')
  await expect(block).toContainText('为满足')
  await expect(block).toContainText('提交缓冲')

  await page.getByRole('button', { name: '接受为当前计划' }).click()
  await expect(page.getByText('当前计划', { exact: true })).toBeVisible()

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
  await page.getByRole('link', { name: taskName }).first().click()
  await expect(page).toHaveURL(/\/tasks\?task=\d+/)
  await expect(page.locator('.task-row.is-highlighted')).toContainText(taskName)
  expect(pageErrors).toEqual([])
})

test('过期草案接受被拒绝后可刷新到最新计划', async ({ page }) => {
  const unique = Date.now().toString().slice(-6)
  const taskName = `过期草案任务 ${unique}`
  const today = new Date()
  const due = new Date(today.getFullYear(), today.getMonth(), today.getDate() + 2)
  const dueDate = dateKey(due)
  const weekday = (due.getDay() + 6) % 7

  await page.goto('/')
  await page.getByLabel('用户名').fill('student')
  await page.getByLabel('密码').fill('campusflow-dev')
  await page.getByLabel('密码').press('Enter')
  await expect(page.getByRole('heading', { name: /今天先做什么/ })).toBeVisible()

  const slots = await page.request.get('/api/v1/availability-slots')
  expect(slots.ok()).toBeTruthy()
  const slotList = (await slots.json()) as Array<{ day_of_week: number; start_time: string; end_time: string }>
  // manual-management 在周六固定创建 09:00–11:00；同日再建 10:00–11:00 会因重叠被拒。
  const hasWindow = slotList.some(
    (slot) => slot.day_of_week === weekday && slot.start_time.slice(0, 5) === '12:00' && slot.end_time.slice(0, 5) === '13:00',
  )
  if (!hasWindow) {
    const created = await page.request.post('/api/v1/availability-slots', {
      data: { day_of_week: weekday, start_time: '12:00', end_time: '13:00' },
    })
    expect(created.ok()).toBeTruthy()
  }
  const task = await page.request.post('/api/v1/tasks', {
    data: {
      title: taskName,
      due_date: dueDate,
      due_time: '14:00',
      priority: 'high',
      estimated_minutes: 60,
      remaining_minutes: 60,
    },
  })
  expect(task.ok()).toBeTruthy()

  await page.getByRole('navigation', { name: '主要导航' }).getByRole('link', { name: '学习计划' }).click()
  await page.getByRole('button', { name: '生成草案' }).click()
  await expect(page.getByText('待接受草案')).toBeVisible()

  const drafts = await page.request.get('/api/v1/plans/drafts')
  expect(drafts.ok()).toBeTruthy()
  const latest = (await drafts.json())[0] as { id: number; version: number }
  const accepted = await page.request.post(`/api/v1/plans/drafts/${latest.id}/accept`, {
    data: { expected_version: latest.version },
  })
  expect(accepted.ok()).toBeTruthy()

  await page.getByRole('button', { name: '接受为当前计划' }).click()
  await expect(page.getByText('操作未完成')).toBeVisible()
  await expect(page.getByRole('button', { name: '重新加载' })).toBeVisible()

  await page.getByRole('button', { name: '重新加载' }).click()
  await expect(page.getByText('当前计划', { exact: true })).toBeVisible()
})

test('计划加载失败后可点击重试恢复', async ({ page }) => {
  await page.goto('/')
  await page.getByLabel('用户名').fill('student')
  await page.getByLabel('密码').fill('campusflow-dev')
  await page.getByLabel('密码').press('Enter')
  await expect(page.getByRole('heading', { name: /今天先做什么/ })).toBeVisible()

  await page.route('**/api/v1/plans/current', (route) => route.abort('failed'))
  await page.getByRole('navigation', { name: '主要导航' }).getByRole('link', { name: '学习计划' }).click()
  await expect(page.getByText('加载失败')).toBeVisible()

  await page.unroute('**/api/v1/plans/current')
  await page.getByRole('button', { name: '重新加载' }).click()
  await expect(page.getByText('加载失败')).toHaveCount(0)
  await expect(page.getByRole('button', { name: '生成草案' })).toBeVisible()
})
