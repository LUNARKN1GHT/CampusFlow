import { expect, test, type Route } from '@playwright/test'

// 课程路径页请求回归：首次进入每个方案数据接口只请求一次；
// 快速切换方案时，旧方案迟到的列表响应不得覆盖最后选中方案的数据。
// 全部接口由 page.route 提供确定性夹具，不依赖本地后端。

type PlanId = 1 | 2

const plans = {
  1: {
    id: 1,
    title: '计算机科学与技术 2024 培养方案',
    major: '计算机科学与技术',
    grade_year: 2024,
    version_label: '2024 版',
    credit_limit_per_term: 24,
    source_material_id: null,
    source_version_id: null,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  },
  2: {
    id: 2,
    title: '计算机科学与技术 2025 培养方案',
    major: '计算机科学与技术',
    grade_year: 2025,
    version_label: '2025 版',
    credit_limit_per_term: 22,
    source_material_id: null,
    source_version_id: null,
    created_at: '2026-01-02T00:00:00Z',
    updated_at: '2026-01-02T00:00:00Z',
  },
} satisfies Record<PlanId, unknown>

const courses = {
  1: [
    {
      id: 11,
      curriculum_plan_id: 1,
      name: '数据结构',
      code: 'CS101',
      credits: 4,
      offered_terms: ['fall'],
      source_candidate_id: null,
      created_at: '2026-01-03T00:00:00Z',
      updated_at: '2026-01-03T00:00:00Z',
    },
  ],
  2: [
    {
      id: 21,
      curriculum_plan_id: 2,
      name: '操作系统',
      code: 'CS201',
      credits: 3,
      offered_terms: ['spring'],
      source_candidate_id: null,
      created_at: '2026-01-04T00:00:00Z',
      updated_at: '2026-01-04T00:00:00Z',
    },
  ],
}

const graphLimitations = { 1: '课程图来自方案 1', 2: '课程图来自方案 2' }
const orderCourseIds = { 1: [11], 2: [21] }
const nextCourseNames = { 1: '算法设计', 2: '编译原理' }

function json(route: Route, body: unknown) {
  return route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify(body),
  })
}

function delay(ms: number) {
  return new Promise<void>((resolve) => setTimeout(resolve, ms))
}

test('课程路径首次进入只请求一次，快速切换方案后保留最后方案数据', async ({ page }) => {
  // 命中记录形如 `courses:1`、`next:2`，用来断言每个方案接口的请求次数。
  const hits: string[] = []
  const count = (label: string) => hits.filter((hit) => hit === label).length

  // 第一轮切换用即时响应；随后给方案 1 的列表接口加延迟，模拟迟到响应。
  let plan1ListDelayMs = 0
  let delayedListFulfilled = 0
  let resolveDelayedLists: (() => void) | null = null
  const delayedListsDone = new Promise<void>((resolve) => {
    resolveDelayedLists = resolve
  })

  await page.route('**/api/v1/**', async (route) => {
    const { pathname } = new URL(route.request().url())

    if (pathname === '/api/v1/auth/session') {
      hits.push('auth/session')
      await json(route, { username: 'student' })
      return
    }
    if (pathname === '/api/v1/learning-profile') {
      hits.push('learning-profile')
      await json(route, {
        workspace_id: 1,
        major: '计算机科学与技术',
        grade_year: 2024,
        curriculum_plan_id: 1,
        missing_fields: [],
        current_plan: plans[1],
      })
      return
    }
    if (pathname === '/api/v1/learning-profile/switch-preview') {
      hits.push('switch-preview')
      const target = plans[2]
      await json(route, {
        current_plan: plans[1],
        target_plan: target,
        relation_count: 0,
        course_count: 1,
        study_record_count: 0,
        note: '切换预览',
      })
      return
    }
    if (pathname === '/api/v1/curriculum-plans') {
      hits.push('curriculum-plans')
      await json(route, [plans[1], plans[2]])
      return
    }
    if (pathname === '/api/v1/semesters') {
      hits.push('semesters')
      await json(route, [])
      return
    }

    const nested = /^\/api\/v1\/curriculum-plans\/(\d+)\/(courses|relations|aliases|study-records|study-status|graph|order|next)$/.exec(
      pathname,
    )
    if (nested) {
      const planId = Number(nested[1]) as PlanId
      const resource = nested[2]
      const label = `${resource}:${planId}`
      hits.push(label)

      const listResources = ['courses', 'relations', 'aliases']
      if (planId === 1 && plan1ListDelayMs > 0 && listResources.includes(resource)) {
        await delay(plan1ListDelayMs)
        await json(route, payloadFor(resource, planId))
        delayedListFulfilled += 1
        if (delayedListFulfilled === listResources.length) resolveDelayedLists?.()
        return
      }

      await json(route, payloadFor(resource, planId))
      return
    }

    await route.fulfill({
      status: 404,
      contentType: 'application/json',
      body: JSON.stringify({ detail: `测试未模拟的接口：${pathname}` }),
    })
  })

  function payloadFor(resource: string, planId: PlanId): unknown {
    switch (resource) {
      case 'courses':
        return courses[planId]
      case 'relations':
      case 'aliases':
      case 'study-records':
        return []
      case 'study-status':
        return {
          plan_id: planId,
          courses: [],
          completed_count: 0,
          in_progress_count: 0,
          planned_count: 0,
          unknown_count: 0,
          note: '',
        }
      case 'graph':
        return {
          plan_id: planId,
          plan_version: plans[planId].version_label,
          nodes: [],
          edges: [],
          cycles: [],
          conflicts: [],
          missing: [],
          pending: [],
          complete: true,
          limitations: [graphLimitations[planId]],
        }
      case 'order':
        return {
          plan_id: planId,
          plan_version: plans[planId].version_label,
          complete: true,
          blocked: false,
          levels: [orderCourseIds[planId]],
          alternatives: false,
          problems: [],
          limitations: [],
        }
      case 'next':
        return {
          plan_id: planId,
          plan_version: plans[planId].version_label,
          complete: true,
          blocked: false,
          courses: [
            {
              course_id: orderCourseIds[planId][0],
              name: nextCourseNames[planId],
              study_status: 'unknown',
              available: true,
              unmet: [],
              recommended: [],
            },
          ],
          limitations: [],
        }
      default:
        return {}
    }
  }

  await page.goto('/course-paths')
  await expect(page.getByRole('heading', { name: '课程路径', level: 1 })).toBeVisible()

  // 首次进入：等方案 1 的列表、修读与三项分析全部渲染后，每个接口都只应命中一次。
  await expect(page.locator('.level-card li').first()).toHaveText('数据结构')
  await expect(page.locator('.next-list strong').first()).toHaveText('算法设计')
  expect(count('auth/session')).toBe(1)
  expect(count('learning-profile')).toBe(1)
  expect(count('curriculum-plans')).toBe(1)
  expect(count('semesters')).toBe(1)
  expect(count('courses:1')).toBe(1)
  expect(count('relations:1')).toBe(1)
  expect(count('aliases:1')).toBe(1)
  expect(count('study-records:1')).toBe(1)
  expect(count('study-status:1')).toBe(1)
  expect(count('graph:1')).toBe(1)
  expect(count('order:1')).toBe(1)
  expect(count('next:1')).toBe(1)

  const planButton = (label: string) => page.locator('.plan-choice', { hasText: label })

  // 先切到方案 2 并等它完整渲染，确认切换路径本身正常。
  await planButton('2025 版').click()
  await expect(page.locator('.level-card li').first()).toHaveText('操作系统')
  await expect(page.locator('.next-list strong').first()).toHaveText('编译原理')

  // 方案 1 的响应延迟返回：点击方案 1 后立刻再点方案 2，
  // 最后选中仍是方案 2，迟到的方案 1 列表必须被丢弃。
  plan1ListDelayMs = 400
  await planButton('2024 版').click()
  await planButton('2025 版').click()
  await delayedListsDone
  await page.waitForTimeout(150)

  const courseTable = page.locator('.data-table').first()
  await expect(courseTable.locator('tbody tr')).toHaveCount(1)
  await expect(courseTable.locator('tbody tr').first()).toContainText('操作系统')
  await expect(courseTable).not.toContainText('数据结构')
  await expect(page.locator('.level-card li').first()).toHaveText('操作系统')
  await expect(page.locator('.next-list strong').first()).toHaveText('编译原理')
  await expect(page.getByText('课程图来自方案 2')).toBeVisible()
  await expect(page.getByText('课程图来自方案 1')).toHaveCount(0)
  await expect(page.getByText('正在加载方案数据…')).toHaveCount(0)
  // 快速切换期间两个方案各至少拉取过一次列表，说明这次测试确实覆盖了并发响应。
  expect(count('courses:1')).toBeGreaterThanOrEqual(2)
  expect(count('courses:2')).toBeGreaterThanOrEqual(2)
})
