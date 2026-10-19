import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'

function dateKey(date: Date): string {
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

function activeText(page: Page) {
  return page.evaluate(() => {
    const element = document.activeElement as HTMLElement | null
    if (!element || element === document.body) return ''
    return (element.getAttribute('aria-label') ?? element.textContent ?? '').replace(/\s+/g, ' ').trim()
  })
}

async function tabUntilText(page: Page, text: string): Promise<void> {
  for (let index = 0; index < 80; index += 1) {
    await page.keyboard.press('Tab')
    if ((await activeText(page)).includes(text)) return
  }
  throw new Error(`键盘 Tab 未到达“${text}”`)
}

async function tabUntilControl(page: Page, labelText: string): Promise<void> {
  for (let index = 0; index < 80; index += 1) {
    await page.keyboard.press('Tab')
    const matched = await page.evaluate((labelText) => {
      const element = document.activeElement as HTMLElement | null
      const label = element?.closest('label')
      return Boolean(label && (label.textContent ?? '').includes(labelText))
    }, labelText)
    if (matched) return
  }
  throw new Error(`键盘 Tab 未到达“${labelText}”输入框`)
}

async function tabUntilButtonInRow(page: Page, rowText: string, buttonText: string): Promise<void> {
  for (let index = 0; index < 120; index += 1) {
    await page.keyboard.press('Tab')
    const matched = await page.evaluate(
      ({ rowText, buttonText }) => {
        const element = document.activeElement as HTMLElement | null
        if (!element || !(element.textContent ?? '').trim().includes(buttonText)) return false
        const row = element.closest('.block-row')
        return Boolean(row && (row.textContent ?? '').includes(rowText))
      },
      { rowText, buttonText },
    )
    if (matched) return
  }
  throw new Error(`键盘 Tab 未到达任务“${rowText}”中的“${buttonText}”`)
}

test('计划关键流程可仅用键盘完成，并保留可见焦点', async ({ page }) => {
  const unique = Date.now().toString().slice(-6)
  const taskName = `键盘计划任务 ${unique}`
  const today = new Date()
  const due = new Date(today.getFullYear(), today.getMonth(), today.getDate() + 1)
  const dueDate = dateKey(due)
  const weekday = (due.getDay() + 6) % 7

  await page.goto('/')
  const selectAll = process.platform === 'darwin' ? 'Meta+A' : 'Control+A'
  await tabUntilControl(page, '用户名')
  await page.keyboard.press(selectAll)
  await page.keyboard.type('student')
  await tabUntilControl(page, '密码')
  await page.keyboard.type('campusflow-dev')
  await tabUntilText(page, '登录')
  await page.keyboard.press('Enter')
  await expect(page.getByRole('heading', { name: /今天先做什么/ })).toBeVisible()

  await tabUntilText(page, '学习计划')
  await page.keyboard.press('Enter')
  await expect(page.getByRole('heading', { name: '两周学习计划' })).toBeVisible()

  const slots = await page.request.get('/api/v1/availability-slots')
  expect(slots.ok()).toBeTruthy()
  const slotList = (await slots.json()) as Array<{ day_of_week: number; start_time: string; end_time: string }>
  const hasWindow = slotList.some(
    (slot) => slot.day_of_week === weekday && slot.start_time.slice(0, 5) === '08:00' && slot.end_time.slice(0, 5) === '09:00',
  )
  if (!hasWindow) {
    const created = await page.request.post('/api/v1/availability-slots', {
      data: { day_of_week: weekday, start_time: '08:00', end_time: '09:00' },
    })
    expect(created.ok()).toBeTruthy()
  }
  const task = await page.request.post('/api/v1/tasks', {
    data: {
      title: taskName,
      due_date: dueDate,
      due_time: '10:00',
      priority: 'high',
      estimated_minutes: 60,
      remaining_minutes: 60,
    },
  })
  expect(task.ok()).toBeTruthy()

  await tabUntilText(page, '生成未来两周草案')
  await page.keyboard.press('Enter')
  await expect(page.getByText('待接受草案')).toBeVisible()

  await tabUntilText(page, '接受为当前计划')
  await page.keyboard.press('Enter')
  await expect(page.getByText('当前计划', { exact: true })).toBeVisible()

  await tabUntilButtonInRow(page, taskName, '锁定')
  const focusRing = await page.evaluate(() => {
    const element = document.activeElement as HTMLElement | null
    if (!element) return null
    const style = getComputedStyle(element)
    return { style: style.outlineStyle, width: style.outlineWidth }
  })
  expect(focusRing?.style).not.toBe('none')
  expect(focusRing?.width).not.toBe('0px')
  await page.keyboard.press('Enter')

  const row = page.locator('.block-row').filter({ hasText: taskName })
  await expect(row.getByText('已锁定')).toBeVisible()
  const originalTime = (await row.locator('.task-title-line strong').first().textContent())?.trim()

  await tabUntilButtonInRow(page, taskName, '调整时间')
  await page.keyboard.press('Enter')
  await tabUntilControl(page, '开始')
  await page.keyboard.press('ArrowRight')
  await page.keyboard.press('ArrowRight')
  await page.keyboard.press('ArrowRight')
  await page.keyboard.press('ArrowDown')
  await tabUntilControl(page, '结束')
  await page.keyboard.press('ArrowRight')
  await page.keyboard.press('ArrowRight')
  await page.keyboard.press('ArrowRight')
  await page.keyboard.press('ArrowDown')
  await tabUntilButtonInRow(page, taskName, '保存')
  await page.keyboard.press('Enter')

  await expect(row.getByText('用户手动移动')).toBeVisible()
  await expect(row.locator('.task-title-line strong').first()).not.toHaveText(originalTime ?? '')
})
