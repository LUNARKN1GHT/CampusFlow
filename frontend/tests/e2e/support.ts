/*
 * E2E 共享辅助：统一登录入口、唯一数据后缀与轻量清理。
 *
 * 背景（Issue #614）：用例直连本地真实后端并共享同一数据库，第二次运行会读到上一次留下的
 * 学期、任务、日程与偏好。因此这里集中凭据，测试数据统一加唯一后缀，清理只做“取消自己创建
 * 的任务／删除自己创建的日程／重新归档自己创建的学期”这类可逆操作，不再依赖“全库为空”。
 * 凭据默认沿用本机开发账号，可用 CAMPUSFLOW_E2E_USER／CAMPUSFLOW_E2E_PASSWORD 覆盖。
 */
import { expect, type Page } from '@playwright/test'

export const e2eAccount = {
  username: process.env.CAMPUSFLOW_E2E_USER ?? 'student',
  password: process.env.CAMPUSFLOW_E2E_PASSWORD ?? 'campusflow-dev',
}

let suffixSeq = 0

/** 跨运行不冲突的唯一后缀：时间戳 + 进程内序号 + 随机片段，避免同一毫秒或定时重跑产生重名。 */
export function uniqueSuffix(): string {
  suffixSeq += 1
  return `${Date.now().toString(36)}${suffixSeq.toString(36)}${Math.random().toString(36).slice(2, 6)}`
}

export function dateKey(date: Date): string {
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

export const weekdayLabels = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'] as const

/** day_of_week 与设置页一致：0 为周一。 */
export function weekdayLabel(dayOfWeek: number): string {
  return weekdayLabels[dayOfWeek] ?? ''
}

/** 已停在登录页时填写凭据并等待进入总览。 */
export async function signIn(page: Page): Promise<void> {
  await page.getByLabel('用户名').fill(e2eAccount.username)
  await page.getByLabel('密码').fill(e2eAccount.password)
  await page.getByLabel('密码').press('Enter')
  await expect(page.getByRole('heading', { name: /今天先做什么/ })).toBeVisible()
}

/** 打开应用并完成本机单用户登录。 */
export async function login(page: Page): Promise<void> {
  await page.goto('/')
  await expect(page.getByLabel('用户名')).toBeVisible()
  await signIn(page)
}

/** 直接调用登录接口（与浏览器上下文共享 Cookie），用于需要先经 API 准备数据的用例。 */
export async function loginViaApi(page: Page): Promise<void> {
  const response = await page.request.post('/api/v1/auth/login', { data: e2eAccount })
  expect(response.ok(), 'E2E 登录接口返回失败').toBeTruthy()
}

export type WorkspaceSettings = {
  timezone: string
  daily_capacity_minutes: number
  weekly_capacity_minutes: number
  break_minutes: number
  buffer_minutes: number
}

export type AvailabilitySlot = {
  id: number
  day_of_week: number
  start_time: string
  end_time: string
}

export async function listAvailabilitySlots(page: Page): Promise<AvailabilitySlot[]> {
  const response = await page.request.get('/api/v1/availability-slots')
  expect(response.ok(), '读取可用时段失败').toBeTruthy()
  return await response.json() as AvailabilitySlot[]
}

/** 可用时段只有星期与时间两端，跨运行会重复；先按接口判断存在性再决定是否通过界面新增。 */
export function hasAvailabilitySlot(slots: AvailabilitySlot[], dayOfWeek: number, start: string, end: string): boolean {
  return slots.some(
    (slot) => slot.day_of_week === dayOfWeek
      && slot.start_time.slice(0, 5) === start
      && slot.end_time.slice(0, 5) === end,
  )
}

/** 每周可用时间面板中的一条时段；限制在可用时段网格内，避免命中 AI 服务等其它 article。 */
export function availabilityEntry(page: Page, dayOfWeek: number, range: string) {
  return page.locator('.availability-grid article')
    .filter({ hasText: weekdayLabel(dayOfWeek) })
    .filter({ hasText: range })
}

/**
 * 在课程页显式选中指定学期。每次刷新或重新进入课程页都会默认选中第一个进行中学期，
 * 共享库里其它学期排在前面时，必须按唯一名称重新选中，不能假设“刚建的学期仍是当前学期”。
 */
export async function selectSemester(page: Page, name: string): Promise<void> {
  const card = page.locator('.semester-card').filter({ hasText: name })
  await expect(card, `学期卡片「${name}」应可见`).toBeVisible()
  await card.locator('.semester-select').click()
  await expect(card).toHaveClass(/selected/)
}

/**
 * 固定工作空间时区为基线（默认 Asia/Shanghai），其它偏好原样保留。
 * 时区是工作空间级持久设置，上一次运行可能已改为 Asia/Tokyo，让“改成东京后 +1 小时”的断言不可重复。
 */
export async function ensureTimezone(page: Page, timezone: string): Promise<void> {
  const response = await page.request.get('/api/v1/settings')
  expect(response.ok(), '读取工作空间设置失败').toBeTruthy()
  const settings = await response.json() as WorkspaceSettings
  if (settings.timezone === timezone) return
  const updated = await page.request.put('/api/v1/settings', {
    data: {
      timezone,
      daily_capacity_minutes: settings.daily_capacity_minutes,
      weekly_capacity_minutes: settings.weekly_capacity_minutes,
      break_minutes: settings.break_minutes,
      buffer_minutes: settings.buffer_minutes,
    },
  })
  expect(updated.ok(), '写入工作空间时区失败').toBeTruthy()
}

/**
 * 用例结束时取消自己创建的任务：取消态不参与排期，避免下一次运行的草案被历史任务挤占可用时间。
 * 任务没有删除接口，清理失败也不改变用例结论。
 */
export async function cancelTaskByTitle(page: Page, title: string): Promise<void> {
  try {
    const response = await page.request.get('/api/v1/tasks')
    if (!response.ok()) return
    const tasks = await response.json() as Array<{ id: number; title: string; progress: string }>
    const task = tasks.find((item) => item.title === title)
    if (!task || task.progress === 'cancelled') return
    await page.request.patch(`/api/v1/tasks/${task.id}/progress`, {
      data: { progress: 'cancelled', reason: 'E2E 用例清理：避免影响下次运行' },
    })
  } catch {
    // 清理是尽力而为：失败时只留下测试数据，不影响本次断言。
  }
}

// 本套件用过的任务标题前缀；只按这些前缀识别历史运行的测试任务，不碰用户自己的任务。
const E2E_TASK_PREFIXES = [
  '计划验收任务 ',
  '过期草案任务 ',
  '键盘计划任务 ',
  '完成迭代报告 ',
  '目标与逾期回归 ',
]

const CANCELABLE_PROGRESS = new Set(['not_started', 'in_progress', 'blocked'])

/**
 * 取消历史运行留下的本套件任务。共享库里残留的旧用例任务会按更早的截止抢占排期窗口，
 * 让新任务的草案被拆成多段甚至排不进去；取消后不参与排期，用例断言才能重复。
 * 只处理本套件用过的标题前缀，且不动已完成／已取消的任务。
 */
export async function cancelLeftoverTestTasks(page: Page): Promise<void> {
  try {
    const response = await page.request.get('/api/v1/tasks')
    if (!response.ok()) return
    const tasks = await response.json() as Array<{ id: number; title: string; progress: string }>
    for (const task of tasks) {
      if (!CANCELABLE_PROGRESS.has(task.progress)) continue
      if (!E2E_TASK_PREFIXES.some((prefix) => task.title.startsWith(prefix))) continue
      await page.request.patch(`/api/v1/tasks/${task.id}/progress`, {
        data: { progress: 'cancelled', reason: 'E2E 用例清理：上次运行留下的测试任务' },
      })
    }
  } catch {
    // 同上：清理失败不影响用例结论。
  }
}

/** 用例结束时删除自己创建的固定日程，避免周视图逐次运行累积。 */
export async function deleteFixedEventsByTitle(page: Page, title: string): Promise<void> {
  try {
    const response = await page.request.get('/api/v1/fixed-events')
    if (!response.ok()) return
    const events = await response.json() as Array<{ id: number; title: string }>
    for (const event of events.filter((item) => item.title === title)) {
      await page.request.delete(`/api/v1/fixed-events/${event.id}`)
    }
  } catch {
    // 同上：清理失败不影响用例结论。
  }
}

/** 用例结束时把自建学期恢复为归档态，避免进行中学期列表逐次运行增长（归档不删除数据）。 */
export async function setSemesterArchivedByName(page: Page, name: string, archived = true): Promise<void> {
  try {
    const response = await page.request.get('/api/v1/semesters?include_archived=true')
    if (!response.ok()) return
    const semesters = await response.json() as Array<{ id: number; name: string; archived: boolean }>
    const semester = semesters.find((item) => item.name === name)
    if (!semester || semester.archived === archived) return
    await page.request.patch(`/api/v1/semesters/${semester.id}`, { data: { archived } })
  } catch {
    // 同上：清理失败不影响用例结论。
  }
}
