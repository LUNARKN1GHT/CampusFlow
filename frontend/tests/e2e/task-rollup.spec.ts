/*
 * 任务卡片汇总回归：子任务进度变化后，父任务卡片的剩余耗时与完成度必须立即刷新。
 *
 * 背景：subtask_rollup 只在任务列表接口里返回，子任务进度／编辑的响应里没有父任务汇总；
 * 修复前标记一个子任务完成后，依赖与子任务面板已是 40 分钟 / 33%，但列表卡片仍是
 * 60 分钟 / 0%，要刷新页面才更新。因此本用例关闭进度面板后不刷新页面就断言父卡片内容。
 *
 * 数据：父任务原始／剩余 60 分钟，三个子任务各 20 分钟；完成其中一个后汇总应为
 * 剩余 40 分钟、完成度 33%。标题带唯一后缀，清理只取消自己创建的任务。
 */
import { expect, test } from '@playwright/test'

import { cancelTaskByTitle, login, uniqueSuffix } from './support'

test('子任务完成后父任务卡片不刷新页面即显示新的剩余耗时与完成度', async ({ page }) => {
  const unique = uniqueSuffix()
  const parentName = `父任务汇总 ${unique}`
  const subtaskNames = [`子任务甲 ${unique}`, `子任务乙 ${unique}`, `子任务丙 ${unique}`]

  await login(page)
  await page.getByRole('link', { name: '任务', exact: true }).click()

  // 新建父任务：原始与当前剩余都记 60 分钟，三个子任务合计同样为 60 分钟。
  await page.getByRole('button', { name: '添加任务' }).click()
  await page.getByLabel('任务标题').fill(parentName)
  await page.getByLabel('原始预计分钟').fill('60')
  await page.getByLabel('当前剩余分钟').fill('60')
  await page.getByRole('button', { name: '保存任务' }).click()

  const parentRow = page.locator('.task-row').filter({
    has: page.getByRole('heading', { name: parentName }),
  })
  await expect(parentRow).toBeVisible()

  // 任务行上的按钮与面板标题同名，面板按 region 角色定位，避免选到任务行里的按钮。
  await parentRow.getByRole('button', { name: '依赖与子任务' }).click()
  const relations = page.getByRole('region', { name: '依赖与子任务' })
  await expect(relations).toBeVisible()

  // 手动添加三行子任务，每行 20 分钟。
  for (let index = 0; index < subtaskNames.length; index += 1) {
    await relations.getByRole('button', { name: '手动添加一行' }).click()
  }
  const titleInputs = relations.getByLabel('子任务标题')
  const minuteInputs = relations.getByLabel('剩余分钟')
  for (const [index, name] of subtaskNames.entries()) {
    await titleInputs.nth(index).fill(name)
    await minuteInputs.nth(index).fill('20')
  }
  await relations.getByRole('button', { name: '按以上内容创建子任务' }).click()
  await expect(relations.getByText(subtaskNames[0]!)).toBeVisible()

  await relations.getByRole('button', { name: '关闭' }).click()
  await expect(relations).toBeHidden()

  // 在主列表里找到第一个子任务，把它的执行进度改为已完成并填写原因。
  const firstSubtaskRow = page.locator('.task-row').filter({
    has: page.getByRole('heading', { name: subtaskNames[0]! }),
  })
  await expect(firstSubtaskRow).toBeVisible()
  await firstSubtaskRow.getByRole('button', { name: '进度' }).click()

  const progressPanel = page.locator('section').filter({ has: page.getByLabel('新状态') })
  await expect(progressPanel).toBeVisible()
  await page.getByLabel('新状态').selectOption('done')
  await page.getByLabel('调整原因').fill('E2E：标记第一个子任务完成')
  await page.getByRole('button', { name: '更新进度' }).click()
  // 历史列表出现本次原因，说明进度已写入后端；此时仍未刷新页面。
  await expect(page.locator('.history-list')).toContainText('E2E：标记第一个子任务完成')
  await progressPanel.getByRole('button', { name: '关闭' }).click()
  await expect(progressPanel).toBeHidden()

  // 不刷新页面：父任务卡片必须已经换成新的汇总（修复前仍显示 60 分钟 / 0%）。
  await expect(parentRow).toContainText('40 分钟剩余（子任务汇总）')
  await expect(parentRow).toContainText('完成度 33%')

  // 刷新后仍一致，确认卡片与后端汇总口径相同。
  await page.reload()
  const reloadedParentRow = page.locator('.task-row').filter({
    has: page.getByRole('heading', { name: parentName }),
  })
  await expect(reloadedParentRow).toContainText('40 分钟剩余（子任务汇总）')
  await expect(reloadedParentRow).toContainText('完成度 33%')

  // 清理：取消父任务与三个子任务，可逆且不影响其它数据。
  for (const name of subtaskNames) await cancelTaskByTitle(page, name)
  await cancelTaskByTitle(page, parentName)
})
