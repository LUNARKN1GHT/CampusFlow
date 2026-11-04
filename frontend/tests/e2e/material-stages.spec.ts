import { expect, test } from '@playwright/test'
import type { JobStage, JobStatus, Material, ProcessingJob } from '../../src/api/types'

test('原文、事项整理与问答状态分开，重试和刷新只显示当前版本', async ({ page }) => {
  let recovered = false
  let progressFailure = false
  const retries: number[] = []
  const materials: Material[] = Array.from({ length: 6 }, (_, i) => ({
    id: i + 1, title: `合成资料${i + 1}`, source_type: i === 1 ? 'text' : 'pdf',
    status: i === 2 ? 'partial' : i === 5 ? 'failed' : 'done', archived: false,
    semester_id: null, course_id: null, class_name: null, publisher: null, published_at: null,
    source_url: null, imported_at: '2026-11-04T08:00:00Z',
  }))
  const job = (id: number, material: number, stage: JobStage, status: JobStatus, version = material): ProcessingJob => ({
    id, material_id: material, version_id: version, stage, status,
    is_terminal: !['pending', 'running'].includes(status), retryable: status === 'failed',
    scope: status === 'partial' ? '第2页未识别' : null,
    error_reason: status === 'failed' ? `${stage} 合成失败原因` : null,
    attempts: 1, enqueued_at: null, started_at: null, created_at: '2026-11-04T08:00:00Z', updated_at: '2026-11-04T08:00:00Z',
  })
  const jobs = () => [
    job(10, 1, 'parse', 'done'), job(11, 1, 'embed', recovered ? 'done' : 'failed'),
    job(30, 3, 'parse', 'partial'), job(31, 3, 'embed', 'partial'),
    job(40, 4, 'parse', 'done'), job(41, 4, 'embed', 'done'), job(42, 4, 'extract', 'failed'),
    // 即使旧版本作业编号更大，也不能覆盖最新版本成功的状态。
    job(99, 5, 'embed', 'failed', 50), job(51, 5, 'embed', 'done', 51),
    job(60, 6, 'parse', 'failed'),
  ]
  await page.route('**/api/v1/**', async (route) => {
    const url = new URL(route.request().url())
    const path = url.pathname.replace('/api/v1', '')
    let body: unknown = []
    if (path === '/auth/session') body = { username: 'synthetic-test' }
    if (path === '/materials') body = materials
    if (path === '/jobs') body = jobs().filter((item) => item.material_id === Number(url.searchParams.get('material_id')))
    const versions = path.match(/^\/materials\/(\d+)\/versions$/)
    if (versions) {
      const id = Number(versions[1])
      body = id === 5 ? [{ id: 50, version_no: 1 }, { id: 51, version_no: 2 }] : [{ id, version_no: 1 }]
    }
    const progress = path.match(/^\/jobs\/(\d+)\/index-progress$/)
    if (progress) {
      if (progressFailure) return route.fulfill({ status: 500, json: {} })
      const id = Number(progress[1])
      const current = jobs().find((item) => item.id === id)!
      body = { job_id: id, manifest_id: 'synthetic', summary: { fully_searchable: current.status === 'done', indexed: current.status === 'failed' ? 0 : 1, expected: id === 31 ? 2 : 1, uncovered_source_count: id === 31 ? 1 : 0, source_coverage_known: true }, blocks: null }
    }
    if (path === '/jobs/11/retry') {
      retries.push(11)
      recovered = true
      body = job(11, 1, 'embed', 'pending')
    }
    await route.fulfill({ json: body })
  })
  await page.goto('/library')
  const row = (id: number) => page.locator('.material-row').filter({ hasText: `合成资料${id}` })
  await expect(row(1)).toContainText('原文读取完成')
  await expect(row(1)).toContainText('资料问答：准备失败')
  await expect(row(1)).toContainText('embed 合成失败原因')
  await expect(row(2)).toContainText('资料问答：未开始')
  await expect(row(2)).toContainText('事项整理：未开始')
  await expect(row(3)).toContainText('部分可用（有未覆盖内容）')
  await expect(row(3)).toContainText('第2页未识别')
  await expect(row(4)).toContainText('事项整理：整理失败')
  await expect(row(4)).toContainText('资料问答：可问答')
  await expect(row(5)).toContainText('资料问答：可问答')
  await expect(row(5)).not.toContainText('合成失败原因')
  await expect(row(6)).toContainText('原文读取失败')
  await row(1).getByRole('button', { name: '重试问答准备' }).click()
  await expect(row(1)).toContainText('资料问答：可问答')
  expect(retries).toEqual([11])
  await page.reload()
  await expect(row(1)).toContainText('资料问答：可问答')
  progressFailure = true
  await page.getByRole('button', { name: '刷新', exact: true }).click()
  await expect(row(1)).toContainText('资料问答：状态未知，请刷新')
})
