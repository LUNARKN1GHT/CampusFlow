import { expect, test } from '@playwright/test'

const candidate = (id: number, title: string) => ({
  id,
  kind: 'task',
  review_status: 'pending',
  title,
  course_id: null,
  semester_id: null,
  class_name: null,
  material_id: 10 + id,
  material_title: title,
  version_no: 1,
  missing_reasons: [],
})

const scope = {
  semester_id: 1,
  semester_name: '2026 秋季',
  course_id: 2,
  course_name: '数据库原理',
  class_name: '一班',
}

const side = (candidateId: number, value: string, quote: string) => ({
  candidate_id: candidateId,
  source_version_id: candidateId,
  present: true,
  value,
  effective_value: value,
  origin: 'document',
  effective_origin: 'document',
  verifiable: true,
  evidence: [{ chunk_id: candidateId, quote, locator_type: 'paragraph', locator_value: 'p:1', verifiable: true }],
})

const diff = {
  left: { candidate_id: 1, source_version_id: 1, material_id: 11, material_title: '旧通知', version_no: 1, material_archived: false, kind: 'task', review_status: 'pending', scope },
  right: { candidate_id: 2, source_version_id: 2, material_id: 12, material_title: '新通知', version_no: 1, material_archived: false, kind: 'task', review_status: 'pending', scope },
  scope: { left: scope, right: scope, differences: [] },
  fields: [
    { name: 'title', category: 'other', status: 'unchanged', left: side(1, '实验报告', '实验报告'), right: side(2, '实验报告', '实验报告') },
    { name: 'due_text', category: 'date', status: 'changed', left: side(1, '下周五', '下周五'), right: side(2, '下周三', '下周三') },
  ],
  summary: { total: 2, unchanged: 1, changed: 1, added: 0, missing: 0, explicitly_removed: 0 },
}

const applicability = {
  status: 'conflict',
  recommended_candidate_id: null,
  review_required: true,
  applied: false,
  relation_id: null,
  relation_kind: null,
  scope_comparison: 'same',
  publication: {
    baseline: { material_id: 11, version_id: 1, publisher: '教务处', published_at: '2026-10-01T10:00:00+08:00', published_text: null },
    comparison: { material_id: 12, version_id: 2, publisher: '教务处', published_at: '2026-10-02T10:00:00+08:00', published_text: null },
  },
  reasons: [{ code: 'missing_replacement_declaration', message: '没有明确的替代声明；导入更晚不能自动覆盖旧通知。' }],
  diff,
}

const impact = {
  summary: { task_count: 1, completed_task_count: 1, locked_block_count: 1, fixed_event_count: 0, rule_count: 0 },
  unsupported: [
    { code: 'reminders', message: '应用内提醒尚未接入，本次不调整或声称已重排提醒。' },
    { code: 'external_calendar', message: '外部日历尚未接入，本次不写入或声称已同步。' },
  ],
  tasks: [{
    id: 5,
    title: '提交实验报告',
    progress: 'done',
    completed: true,
    due_date: '2026-11-15',
    source_state: 'active',
    plan_blocks: [{ id: 9, draft_id: 3, draft_status: 'draft', title: '实验报告时间块', starts_at: '2026-11-14T09:00:00Z', ends_at: '2026-11-14T10:00:00Z', locked: true }],
  }],
  fixed_events: [],
  rules: [],
}

test('版本对照展示范围、差异、影响，未接受前不提交变更', async ({ page }) => {
  const posts: unknown[] = []
  await page.route('**/api/v1/candidates?**', (route) => route.fulfill({ json: [candidate(1, '旧通知'), candidate(2, '新通知')] }))
  await page.route('**/api/v1/candidates/1/diff/2/applicability', (route) => route.fulfill({ json: applicability }))
  await page.route('**/api/v1/candidates/1/diff/2/impact', (route) => route.fulfill({ json: impact }))
  await page.route('**/api/v1/candidates/1/diff/2/accept', (route) => {
    posts.push(route.request().postDataJSON())
    return route.fulfill({ status: 201, json: {
      id: 1, baseline_candidate_id: 1, comparison_candidate_id: 2, baseline_version_id: 1, comparison_version_id: 2,
      accepted_fields: ['due_text'], reason: '按修订通知接受', operator: 'student', evidence_snapshot: [],
      previous_review_status: 'pending', created_at: '2026-10-19T00:00:00Z', baseline_review_status: 'superseded', created: true,
    } })
  })
  await page.route('**/api/v1/candidates/1/diff/2', (route) => route.fulfill({ json: diff }))
  await page.route('**/api/v1/candidates/1/acceptances', (route) => route.fulfill({ json: [] }))

  await page.goto('/login')
  await page.getByLabel('用户名').fill('student')
  await page.getByLabel('密码').fill('campusflow-dev')
  await page.getByLabel('密码').press('Enter')
  await page.goto('/review/changes?baseline=1')
  await page.getByLabel('待比较（新）').selectOption('2')
  await page.getByRole('button', { name: '加载对照' }).click()

  await expect(page.getByText('2026 秋季 · 数据库原理 · 一班').first()).toBeVisible()
  await expect(page.getByText('没有明确的替代声明')).toBeVisible()
  await expect(page.getByText('提交实验报告')).toBeVisible()
  await expect(page.getByText(/已锁定/)).toBeVisible()
  await expect(page.getByText(/应用内提醒尚未接入/)).toBeVisible()
  expect(posts).toHaveLength(0)

  await page.getByLabel('我已核对冲突并确认接受').check()
  await page.getByLabel('接受原因').fill('按修订通知接受')
  await page.getByRole('button', { name: '接受所选差异' }).click()
  await expect(page.getByText(/已接受：due_text/)).toBeVisible()
  expect(posts).toEqual([{ field_names: ['due_text'], reason: '按修订通知接受', acknowledge_conflict: true }])
})

test('重复建议只在预览后合并，取消或失败不隐藏原候选', async ({ page }) => {
  const mergeBodies: unknown[] = []
  let failNext = false
  await page.route('**/api/v1/candidates?**', (route) => route.fulfill({ json: [candidate(1, '旧通知'), candidate(3, '重复通知')] }))
  await page.route('**/api/v1/candidates/1/duplicates', (route) => route.fulfill({ json: {
    candidate_id: 1,
    kind: 'task',
    suggestions: [{
      candidate_id: 3,
      source_version_id: 3,
      title: '重复通知',
      score: 0.85,
      reasons: ['标题相同', '时间字段相同'],
      matched_fields: ['title', 'due_text'],
      review_status: 'pending',
      scope_same: true,
    }],
    excluded: [],
    auto_merge: false,
  } }))
  await page.route('**/api/v1/candidates/1/merge-preview?**', (route) => route.fulfill({ json: {
    target_candidate_id: 1,
    kind: 'task',
    target_title: '旧通知',
    target_formal_id: 5,
    target_progress: 'not_started',
    kept_fields: { title: '旧通知', due_text: '下周五' },
    sources: [{
      candidate_id: 3,
      version_id: 3,
      title: '重复通知',
      review_status: 'pending',
      formal_kind: 'task',
      formal_id: 6,
      progress: 'done',
      plan_block_count: 1,
    }],
    token: 'preview-token',
    auto_merge: false,
  } }))
  await page.route('**/api/v1/candidates/1/merge', (route) => {
    mergeBodies.push(route.request().postDataJSON())
    if (failNext) {
      failNext = false
      return route.fulfill({ status: 500, json: { detail: '服务暂时不可用' } })
    }
    return route.fulfill({ json: {
      records: [{
        id: 1,
        target_candidate_id: 1,
        source_candidate_id: 3,
        target_formal_kind: 'task',
        target_formal_id: 5,
        source_formal_id: 6,
        preserved_progress: { target_progress: 'not_started', source_progress: 'done' },
        source_snapshot: { candidate_id: 3 },
        reason: '同一份作业',
        operator: 'student',
        created_at: '2026-10-19T00:00:00Z',
      }],
      created: true,
      preview: { target_candidate_id: 1, kind: 'task', target_title: '旧通知', target_formal_id: 5, target_progress: 'not_started', kept_fields: {}, sources: [], token: 'preview-token', auto_merge: false },
    } })
  })
  await page.route('**/api/v1/candidates/1/merges', (route) => route.fulfill({ json: [] }))
  await page.route('**/api/v1/candidates/1/acceptances', (route) => route.fulfill({ json: [] }))
  await page.route('**/api/v1/candidates/1/diff/3', (route) => route.fulfill({ json: diff }))
  await page.route('**/api/v1/candidates/1/diff/3/applicability', (route) => route.fulfill({ json: applicability }))
  await page.route('**/api/v1/candidates/1/diff/3/impact', (route) => route.fulfill({ json: impact }))

  await page.goto('/login')
  await page.getByLabel('用户名').fill('student')
  await page.getByLabel('密码').fill('campusflow-dev')
  await page.getByLabel('密码').press('Enter')
  await page.goto('/review/changes?baseline=1')
  await page.getByLabel('待比较（新）').selectOption('3')
  await page.getByRole('button', { name: '加载对照' }).click()

  await expect(page.getByText('相似度 0.85')).toBeVisible()
  await page.getByRole('checkbox', { name: '#3 · 重复通知' }).check()
  await page.getByRole('button', { name: '预览合并' }).click()
  await expect(page.getByText(/来源进度 done/)).toBeVisible()
  await expect(page.getByText(/时间块 1 个/)).toBeVisible()
  expect(mergeBodies).toHaveLength(0)

  await page.getByRole('button', { name: '取消预览' }).click()
  await expect(page.getByRole('button', { name: '确认合并预览范围' })).toHaveCount(0)
  await expect(page.getByText('相似度 0.85')).toBeVisible()

  await page.getByRole('button', { name: '预览合并' }).click()
  await page.getByLabel('合并原因').fill('同一份作业')
  failNext = true
  await page.getByRole('button', { name: '确认合并预览范围' }).click()
  await expect(page.getByText('服务暂时不可用')).toBeVisible()
  await expect(page.getByText('相似度 0.85')).toBeVisible()

  await page.getByRole('button', { name: '确认合并预览范围' }).click()
  await expect(page.getByText(/已合并 1 项/)).toBeVisible()
  expect(mergeBodies).toHaveLength(2)
  expect(mergeBodies[1]).toEqual({ source_candidate_ids: [3], preview_token: 'preview-token', reason: '同一份作业' })
})
