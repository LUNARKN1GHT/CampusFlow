import { expect, test } from '@playwright/test'

function dateKey(date: Date): string {
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

test('手动管理流程使用真实 API 持久化并覆盖失败与键盘操作', async ({ page }) => {
  const unique = Date.now().toString().slice(-6)
  const semesterName = `验收学期 ${unique}`
  const courseName = `软件工程 ${unique}`
  const taskName = `完成迭代报告 ${unique}`
  const eventName = `项目讨论 ${unique}`
  const today = new Date()
  const dueDate = dateKey(new Date(today.getFullYear(), today.getMonth(), today.getDate() + 5))
  const eventDate = dateKey(today)

  await page.goto('/')
  await expect(page).toHaveURL(/\/login/)
  await page.getByLabel('用户名').fill('student')
  await page.getByLabel('密码').fill('campusflow-dev')
  await page.getByLabel('密码').press('Enter')
  await expect(page.getByRole('heading', { name: '让学习安排，' })).toBeVisible()

  await page.getByRole('link', { name: '课程' }).click()
  await expect(page.getByText('没有进行中的学期')).toBeVisible()
  await page.getByLabel('学期名称').fill(semesterName)
  await page.getByLabel('开始日期').fill(dateKey(new Date(today.getFullYear(), 8, 1)))
  await page.getByLabel('结束日期').fill(dateKey(new Date(today.getFullYear() + 1, 0, 31)))
  await page.getByRole('button', { name: '保存学期' }).click()
  await expect(page.getByText(semesterName, { exact: true }).first()).toBeVisible()

  await page.getByLabel('课程名称').fill(courseName)
  await page.getByLabel('课程代码').fill(`SE${unique}`)
  await page.getByLabel('教学班').fill('2 班')
  await page.getByLabel('教师').fill('陈老师')
  await page.getByRole('button', { name: '保存课程' }).click()
  await expect(page.getByRole('heading', { name: courseName })).toBeVisible()
  await page.reload()
  await expect(page.getByRole('heading', { name: courseName })).toBeVisible()

  await page.getByRole('link', { name: '任务与计划' }).click()
  await expect(page.getByText('还没有任务')).toBeVisible()
  await page.getByRole('button', { name: '添加任务' }).click()
  await page.getByLabel('任务标题').fill(taskName)
  await page.getByLabel('关联课程').first().selectOption({ label: `${courseName} · 2 班` })
  await page.getByLabel('正式 DDL 日期').fill(dueDate)
  await page.getByLabel('优先级').selectOption('high')
  await page.getByLabel('原始预计分钟').fill('120')
  await page.getByLabel('当前剩余分钟').fill('90')
  await page.getByRole('button', { name: '保存任务' }).click()
  await expect(page.getByText(`${dueDate}（仅日期）`)).toBeVisible()
  await expect(page.locator('.task-row').filter({ hasText: taskName }).locator('.task-meta strong')).toContainText('90')

  await page.getByRole('button', { name: '添加日程' }).click()
  await page.getByLabel('标题').fill(eventName)
  await page.getByLabel('开始时间').fill(`${eventDate}T10:00`)
  await page.getByLabel('结束时间').fill(`${eventDate}T11:00`)
  await page.getByLabel('地点').fill('图书馆 3F')
  await page.getByRole('button', { name: '保存日程' }).click()
  await expect(page.getByText(eventName).first()).toBeVisible()

  const taskRow = page.locator('.task-row').filter({ hasText: taskName })
  await taskRow.getByRole('button', { name: '进度' }).click()
  await page.getByLabel('新状态').selectOption('blocked')
  await page.getByLabel('调整原因').fill('等待小组数据')
  await page.getByRole('button', { name: '更新进度' }).click()
  await expect(page.getByText('等待小组数据')).toBeVisible()

  await page.getByRole('link', { name: '设置' }).click()
  await page.getByLabel('每日可学习容量（分钟）').fill('210')
  await page.getByRole('button', { name: '保存设置' }).click()
  await expect(page.getByText('学习偏好已保存')).toBeVisible()
  await page.getByLabel('星期').selectOption('5')
  await page.getByLabel('开始').fill('09:00')
  await page.getByLabel('结束').fill('11:00')
  await page.getByRole('button', { name: '添加时段' }).click()
  await expect(page.locator('.availability-grid article').filter({ hasText: '周六' })).toBeVisible()

  await page.getByRole('link', { name: '任务与计划' }).click()
  await page.reload()
  const persistedRow = page.locator('.task-row').filter({ hasText: taskName })
  await expect(persistedRow.getByText('受阻')).toBeVisible()

  await page.route('**/api/v1/semesters?**', (route) => route.fulfill({ status: 500, body: '{}' }))
  await page.getByRole('link', { name: '课程' }).click()
  await expect(page.getByRole('alert')).toContainText('服务暂时不可用')
  await page.unroute('**/api/v1/semesters?**')
  await page.getByRole('button', { name: '重新加载' }).click()
  await expect(page.getByRole('heading', { name: courseName })).toBeVisible()

  await page.getByRole('button', { name: '退出' }).click()
  await expect(page).toHaveURL(/\/login/)
  await page.goto('/tasks')
  await expect(page).toHaveURL(/\/login/)
})

test.describe('M1 发布边界回归', () => {
  // 浏览器与工作空间时区不同，验证按空间日期而非浏览器日期切分。
  test.use({ timezoneId: 'America/Los_Angeles' })

  test('跨日跨周日程按实际占用显示，午夜结束不占用次日', async ({ page }) => {
    await page.clock.install({ time: new Date('2026-10-12T12:00:00+09:00') })
    await page.request.post('/api/v1/auth/login', { data: { username: 'student', password: 'campusflow-dev' } })
    const previousSettings = await (await page.request.get('/api/v1/settings')).json()
    await page.request.put('/api/v1/settings', { data: { ...previousSettings, timezone: 'Asia/Tokyo' } })
    const ids: number[] = []
    const suffix = Date.now()
    const events = [
      { title: `跨周 ${suffix}`, starts_at: '2026-10-11T23:00:00', ends_at: '2026-10-12T01:00:00', recurrence: 'weekly', repeat_until: '2026-10-18' },
      { title: `多日 ${suffix}`, starts_at: '2026-10-13T23:00:00', ends_at: '2026-10-15T01:00:00' },
      { title: `午夜结束 ${suffix}`, starts_at: '2026-10-16T22:00:00', ends_at: '2026-10-17T00:00:00' },
    ]
    try {
      for (const event of events) {
        const response = await page.request.post('/api/v1/fixed-events', { data: event })
        expect(response.status()).toBe(201)
        ids.push((await response.json()).id)
      }
      await page.goto('/tasks')
      const day = (date: string) => page.locator('.week-day').filter({ hasText: date })
      const occurrence = (date: string, title: string) => day(date).locator('.day-events article').filter({ hasText: title })
      await expect(occurrence('10/12', events[0]!.title)).toContainText('00:00–01:00')
      await expect(occurrence('10/18', events[0]!.title)).toContainText('23:00–24:00')
      await expect(occurrence('10/13', events[1]!.title)).toContainText('23:00–24:00')
      await expect(occurrence('10/14', events[1]!.title)).toContainText('00:00–24:00')
      await expect(occurrence('10/15', events[1]!.title)).toContainText('00:00–01:00')
      await expect(occurrence('10/16', events[2]!.title)).toContainText('22:00–24:00')
      await expect(day('10/17').getByText(events[2]!.title)).toHaveCount(0)
      await page.getByRole('button', { name: '下一周' }).click()
      await expect(occurrence('10/19', events[0]!.title)).toContainText('00:00–01:00')
      const saved = await (await page.request.get('/api/v1/fixed-events')).json()
      expect(new Date(saved.find((event: { id: number }) => event.id === ids[0]).starts_at).toISOString()).toBe('2026-10-11T14:00:00.000Z')
    } finally {
      for (const id of ids) await page.request.delete(`/api/v1/fixed-events/${id}`)
      await page.request.put('/api/v1/settings', { data: previousSettings })
    }
  })

  test('目标与来源可保存刷新，逾期不覆盖进度，完成后取消逾期', async ({ page }) => {
    await page.request.post('/api/v1/auth/login', { data: { username: 'student', password: 'campusflow-dev' } })
    const title = `目标与逾期回归 ${Date.now()}`
    await page.goto('/tasks')
    await page.getByRole('button', { name: '添加任务' }).click()
    await page.getByLabel('任务标题').fill(title)
    await page.getByLabel('正式 DDL 日期').fill('2000-10-20')
    await page.getByLabel('个人目标日期').fill('2000-10-18')
    await page.getByLabel('截止来源', { exact: true }).selectOption('document')
    await page.getByLabel('截止来源说明').fill('合成课程大纲第 3 页，用户已核对')
    await page.getByRole('button', { name: '保存任务' }).click()
    const row = page.locator('.task-row').filter({ hasText: title })
    await expect(row.getByText('已逾期', { exact: true })).toBeVisible()
    const tasks = await (await page.request.get('/api/v1/tasks')).json()
    const task = tasks.find((item: { title: string }) => item.title === title)
    expect(task.target_date).toBe('2000-10-18')
    expect(task.due_source).toBe('document')
    await page.request.patch(`/api/v1/tasks/${task.id}/progress`, { data: { progress: 'blocked', reason: '等待资料' } })
    await page.reload()
    await expect(row.getByText('受阻', { exact: true })).toBeVisible()
    await expect(row.getByText('已逾期', { exact: true })).toBeVisible()
    await expect(row).toContainText('截止来源：原文规定 · 合成课程大纲第 3 页，用户已核对')
    await row.getByRole('button', { name: '编辑', exact: true }).click()
    await expect(page.getByLabel('个人目标日期')).toHaveValue('2000-10-18')
    await page.getByLabel('个人目标日期').fill('2000-10-19')
    await page.getByRole('button', { name: '保存任务' }).click()
    await page.reload()
    await expect(row).toContainText('个人目标：2000-10-19')
    const updated = await (await page.request.get(`/api/v1/tasks/${task.id}`)).json()
    expect(updated.due_date).toBe('2000-10-20')
    expect(updated.due_time).toBeNull()
    expect(updated.due_note).toBe('合成课程大纲第 3 页，用户已核对')
    await row.getByRole('button', { name: '进度', exact: true }).click()
    await page.getByLabel('新状态').selectOption('done')
    await page.getByRole('button', { name: '更新进度' }).click()
    await expect(row.getByText('已完成', { exact: true })).toBeVisible()
    await expect(row.getByText('已逾期', { exact: true })).toHaveCount(0)
    await row.getByRole('button', { name: '编辑', exact: true }).click()
    await page.getByLabel('个人目标日期').fill('')
    await page.getByRole('button', { name: '保存任务' }).click()
    await page.reload()
    await expect(row.getByText(/个人目标：/)).toHaveCount(0)
    const cleared = await (await page.request.get(`/api/v1/tasks/${task.id}`)).json()
    expect(cleared.target_date).toBeNull()
    expect(cleared.due_date).toBe('2000-10-20')
  })
})
