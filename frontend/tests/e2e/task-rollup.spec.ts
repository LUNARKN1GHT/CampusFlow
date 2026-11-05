import { expect, test, type Page } from '@playwright/test'
import type { Task, TaskProgress, TaskRollup } from '../../src/api/types'

// 只模拟 HTTP 响应，验证真实页面显示及交互；不访问用户数据库或模型服务。
async function fixture(page: Page) {
  const task = (id: number, title: string, minutes: number, parent: number | null = null): Task => ({
    id, title, estimated_minutes: minutes, remaining_minutes: minutes, parent_task_id: parent,
    progress: 'not_started', priority: 'medium', course_id: null, description: null,
    due_date: null, due_time: null, target_date: null, due_source: 'user', due_note: null,
    overdue: false, source_state: 'none', source_invalidated_at: null, source_invalidated_note: null,
    created_at: '2026-11-03T08:00:00Z', updated_at: '2026-11-03T08:00:00Z',
  })
  const tasks = [task(1, '合成父任务', 60), task(2, '阅读资料', 20, 1), task(3, '撰写报告', 40, 1), task(4, '独立练习', 15)]
  let failRollup = false
  const rollup = (): TaskRollup => ({
    subtask_count: 2, estimated_minutes: 60,
    remaining_minutes: tasks[1]!.remaining_minutes! + tasks[2]!.remaining_minutes!,
    missing_duration_count: 0, progress_counts: {}, percent_complete: null,
  })
  await page.route('**/api/v1/**', async (route) => {
    const path = new URL(route.request().url()).pathname.replace('/api/v1', '')
    let body: unknown = []
    if (path === '/auth/session') body = { username: 'synthetic-test' }
    if (path === '/settings') body = { timezone: 'Asia/Shanghai' }
    if (path === '/tasks') body = tasks
    if (path === '/tasks/1/rollup') {
      if (failRollup) return route.fulfill({ status: 500, json: {} })
      body = rollup()
    }
    if (path === '/tasks/1/relations') body = {
      task_id: 1, parent: null, prerequisites: [], dependents: [], subtasks: tasks.slice(1, 3), rollup: rollup(),
    }
    const progressMatch = path.match(/^\/tasks\/(\d+)\/progress$/)
    if (progressMatch) {
      const target = tasks.find((item) => item.id === Number(progressMatch[1]))!
      target.progress = route.request().postDataJSON().progress as TaskProgress
      target.remaining_minutes = ['done', 'cancelled'].includes(target.progress) ? 0 : target.estimated_minutes
      body = target
    }
    if (path === '/tasks/3' && route.request().method() === 'PATCH') {
      Object.assign(tasks[2]!, route.request().postDataJSON())
      body = tasks[2]
    }
    if (path === '/tasks/dependencies/check') body = { valid: true, cycles: [], blocked_tasks: [] }
    return route.fulfill({ json: body })
  })
  return { tasks, setFail: (value: boolean) => { failRollup = value } }
}

test('父卡片与详情在完成、重开、取消及修改耗时后同步，筛选不改变总数', async ({ page }) => {
  const { tasks } = await fixture(page)
  await page.goto('/tasks')
  const parent = page.locator('[data-task-id="1"]')
  const child = page.locator('[data-task-id="2"]')
  const details = page.getByRole('region', { name: '依赖与子任务' })
  await expect(parent.locator('strong')).toContainText('60')
  await parent.getByRole('button', { name: '依赖与子任务' }).click()
  for (const [progress, remaining, label] of [
    ['done', 40, '已完成'], ['not_started', 60, '未开始'], ['cancelled', 40, '已取消'],
  ] as const) {
    await child.getByRole('button', { name: '进度', exact: true }).click()
    await page.getByLabel('新状态').selectOption(progress)
    await page.getByRole('button', { name: '更新进度' }).click()
    await expect(parent.locator('strong')).toContainText(`${remaining} 分钟剩余`)
    await expect(details).toContainText(`剩余 ${remaining} 分钟`)
    await expect(details).toContainText(`阅读资料 · ${label}`)
  }
  await page.locator('.filter-bar').getByRole('combobox').nth(2).selectOption('not_started')
  await expect(child).toHaveCount(0)
  await expect(parent.locator('strong')).toContainText('40 分钟剩余')
  await details.getByRole('listitem').filter({ hasText: '撰写报告' }).getByRole('button', { name: '编辑' }).click()
  await page.getByLabel('当前剩余分钟').fill('30')
  await page.getByRole('button', { name: '保存任务' }).click()
  await expect(parent.locator('strong')).toContainText('30 分钟剩余')
  await expect(details).toContainText('剩余 30 分钟')
  await expect(parent).toContainText('父任务原始预计：60 分钟')
  expect(tasks[0]!.remaining_minutes).toBe(60)
  await page.reload()
  await expect(parent.locator('strong')).toContainText('30 分钟剩余')
  await expect(page.locator('[data-task-id="4"] strong')).toContainText('15 分钟剩余')
})

test('汇总读取失败不冒用父任务原值，可重新加载', async ({ page }) => {
  const { setFail } = await fixture(page)
  setFail(true)
  await page.goto('/tasks')
  const parent = page.locator('[data-task-id="1"]')
  await expect(parent.locator('strong')).toContainText('—')
  await expect(page.getByRole('alert')).toContainText('部分父任务汇总未能读取')
  setFail(false)
  await page.getByRole('button', { name: '重新加载汇总' }).click()
  await expect(parent.locator('strong')).toContainText('60 分钟剩余')
})
