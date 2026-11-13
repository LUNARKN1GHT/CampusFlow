import { expect, test } from '@playwright/test'

import {
  availabilityEntry,
  cancelTaskByTitle,
  dateKey,
  deleteFixedEventsByTitle,
  ensureTimezone,
  loginViaApi,
  selectSemester,
  setSemesterArchivedByName,
  signIn,
  uniqueSuffix,
} from './support'

test('手动管理流程使用真实 API 持久化并覆盖失败与键盘操作', async ({ page }) => {
  const unique = uniqueSuffix()
  const semesterName = `验收学期 ${unique}`
  const courseName = `软件工程 ${unique}`
  const taskName = `完成迭代报告 ${unique}`
  const eventName = `项目讨论 ${unique}`
  const today = new Date()
  const dueDate = dateKey(new Date(today.getFullYear(), today.getMonth(), today.getDate() + 5))
  const eventDate = dateKey(today)

  await page.goto('/')
  await expect(page).toHaveURL(/\/login/)
  await signIn(page)
  // 时区可能在之前的运行里被改成 Asia/Tokyo；先固定基线，后面“改成东京后 +1 小时”才可重复。
  await ensureTimezone(page, 'Asia/Shanghai')

  // 共享数据库里可能已有其它学期：直接创建唯一命名的学期，之后只对这张学期卡片操作。
  await page.getByRole('link', { name: '课程', exact: true }).click()
  await page.getByLabel('学期名称').fill(semesterName)
  await page.getByLabel('开始日期').fill(dateKey(new Date(today.getFullYear(), 8, 1)))
  await page.getByLabel('结束日期').fill(dateKey(new Date(today.getFullYear() + 1, 0, 31)))
  await page.getByRole('button', { name: '保存学期' }).click()
  await expect(page.getByText(semesterName, { exact: true }).first()).toBeVisible()

  const semesterCard = page.locator('.semester-card').filter({ hasText: semesterName })
  await expect(semesterCard).toBeVisible()

  await page.getByLabel('课程名称').fill(courseName)
  await page.getByLabel('课程代码').fill(`SE${unique}`)
  await page.getByLabel('教学班').fill('2 班')
  await page.getByLabel('教师').fill('陈老师')
  await page.getByRole('button', { name: '保存课程' }).click()
  await expect(page.getByRole('heading', { name: courseName })).toBeVisible()
  await page.reload()
  // 刷新后课程页会默认选中第一个进行中学期，这里按唯一名称重新选中本学期再看课程。
  await selectSemester(page, semesterName)
  await expect(page.getByRole('heading', { name: courseName })).toBeVisible()

  // 不再要求全库没有任务：标题带唯一后缀，后续断言都限定在本行内。
  await page.getByRole('link', { name: '任务', exact: true }).click()
  await page.getByRole('button', { name: '添加任务' }).click()
  await page.getByLabel('任务标题').fill(taskName)
  await page.getByLabel('关联课程').first().selectOption({ label: `${courseName} · 2 班` })
  await page.getByLabel('正式 DDL 日期').fill(dueDate)
  await page.getByLabel('优先级').selectOption('high')
  await page.getByLabel('原始预计分钟').fill('120')
  await page.getByLabel('当前剩余分钟').fill('90')
  await page.getByRole('button', { name: '保存任务' }).click()
  const taskRow = page.locator('.task-row').filter({ hasText: taskName })
  await expect(taskRow.getByText(`${dueDate}（仅日期）`)).toBeVisible()
  await expect(taskRow.locator('.task-meta strong')).toContainText('90')

  await page.getByRole('button', { name: '添加日程' }).click()
  await page.getByLabel('标题').fill(eventName)
  await page.getByLabel('开始时间').fill(`${eventDate}T10:00`)
  await page.getByLabel('结束时间').fill(`${eventDate}T11:00`)
  await page.getByLabel('地点').fill('图书馆 3F')
  await page.getByRole('button', { name: '保存日程' }).click()
  await expect(page.getByText(eventName).first()).toBeVisible()

  await taskRow.getByRole('button', { name: '进度' }).click()
  await page.getByLabel('新状态').selectOption('blocked')
  await page.getByLabel('调整原因').fill('等待小组数据')
  await page.getByRole('button', { name: '更新进度' }).click()
  await expect(page.locator('.history-list')).toContainText('等待小组数据')

  await page.getByRole('link', { name: '设置' }).click()
  await page.getByLabel('每日可学习容量（分钟）').fill('210')
  await page.getByLabel('每周可学习容量（分钟）').fill('900')
  await page.getByLabel('时区', { exact: true }).fill('Asia/Tokyo')
  await page.getByRole('button', { name: '保存设置' }).click()
  await expect(page.getByText('学习偏好已保存')).toBeVisible()
  await page.reload()
  await expect(page.getByLabel('每周可学习容量（分钟）')).toHaveValue('900')
  await expect(page.getByLabel('时区', { exact: true })).toHaveValue('Asia/Tokyo')
  await page.getByLabel('星期').selectOption('5')
  await page.getByLabel('开始').fill('09:00')
  await page.getByLabel('结束').fill('11:00')
  await page.getByRole('button', { name: '添加时段' }).click()
  // 再次运行时该时段已存在，服务端会拒绝重复；断言只看时段在列表中可见，不依赖创建次数。
  await expect(availabilityEntry(page, 5, '09:00–11:00')).toBeVisible()

  await page.getByRole('link', { name: '任务', exact: true }).click()
  await expect(page).toHaveURL(/\/tasks$/)
  await page.reload()
  const persistedRow = page.locator('.task-row').filter({ hasText: taskName })
  await expect(persistedRow.getByText('受阻')).toBeVisible()
  await expect(page.getByText('固定日程 · Asia/Tokyo')).toBeVisible()
  const eventDefinition = page.locator('.definition-list article').filter({ hasText: eventName })
  await eventDefinition.getByRole('button', { name: '编辑' }).click()
  await expect(page.getByLabel('开始时间')).toHaveValue(`${eventDate}T11:00`)
  await page.getByLabel('地点').fill('图书馆 4F')
  await page.getByRole('button', { name: '保存日程' }).click()
  const eventsResponse = await page.request.get('/api/v1/fixed-events')
  const savedEvent = (await eventsResponse.json() as { title: string; starts_at: string }[]).find((item) => item.title === eventName)
  expect(new Date(savedEvent!.starts_at).toISOString()).toBe(`${eventDate}T02:00:00.000Z`)

  await page.route('**/api/v1/semesters?**', (route) => route.fulfill({ status: 500, body: '{}' }))
  await page.getByRole('link', { name: '课程', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('服务暂时不可用')
  await page.unroute('**/api/v1/semesters?**')
  await page.getByRole('button', { name: '重新加载' }).click()
  // 重新加载会退回第一个进行中学期；再次按名称选中本学期，课程列表与归档按钮才对应。
  await selectSemester(page, semesterName)
  await expect(page.getByRole('heading', { name: courseName })).toBeVisible()

  // 归档只点本学期卡片上的按钮：共享库里可能还有其它进行中学期。
  await semesterCard.getByRole('button', { name: '归档', exact: true }).click()
  // 其它学期可能仍在进行中，所以归档后只断言本学期默认隐藏，而不是“全库没有进行中学期”。
  await expect(page.locator('.semester-card').filter({ hasText: semesterName })).toHaveCount(0)
  await page.getByLabel('显示历史学期').check()
  const archivedCard = page.locator('.semester-card').filter({ hasText: semesterName })
  await expect(archivedCard).toBeVisible()
  await selectSemester(page, semesterName)
  await expect(page.getByRole('heading', { name: courseName })).toBeVisible()
  await expect(page.getByText('正在查看已归档学期')).toBeVisible()
  await page.getByLabel('显示历史学期').uncheck()
  // 关闭历史视图会退回进行中学期：本学期课程不再显示，也不再提示正在查看归档学期。
  await expect(page.locator('.semester-card').filter({ hasText: semesterName })).toHaveCount(0)
  await expect(page.getByRole('heading', { name: courseName })).toHaveCount(0)
  await expect(page.getByText('正在查看已归档学期')).toHaveCount(0)
  await page.getByRole('link', { name: '任务', exact: true }).click()
  await page.locator('.filter-bar').getByRole('combobox').first().selectOption({ label: `${semesterName}（已归档）` })
  await expect(page.locator('.task-row').filter({ hasText: taskName }).getByText('受阻')).toBeVisible()
  await page.getByRole('link', { name: '课程', exact: true }).click()
  await page.getByLabel('显示历史学期').check()
  const restoreCard = page.locator('.semester-card').filter({ hasText: semesterName })
  await restoreCard.getByRole('button', { name: '恢复', exact: true }).click()
  await selectSemester(page, semesterName)
  await expect(page.getByRole('heading', { name: courseName })).toBeVisible()

  // 清理本次数据：取消任务与删除日程可逆，不删除课程或学期；学期恢复归档态避免进行中列表继续增长。
  await cancelTaskByTitle(page, taskName)
  await deleteFixedEventsByTitle(page, eventName)
  await setSemesterArchivedByName(page, semesterName)

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
    await loginViaApi(page)
    const previousSettings = await (await page.request.get('/api/v1/settings')).json()
    await page.request.put('/api/v1/settings', { data: { ...previousSettings, timezone: 'Asia/Tokyo' } })
    const ids: number[] = []
    const suffix = uniqueSuffix()
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
    await loginViaApi(page)
    const title = `目标与逾期回归 ${uniqueSuffix()}`
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
