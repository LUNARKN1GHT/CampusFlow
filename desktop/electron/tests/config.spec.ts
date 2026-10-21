import { mkdtempSync, rmSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'

import { expect, test } from '@playwright/test'

import { loadConfig } from '../src/config'

const tempDirs: string[] = []

function configFor(env: NodeJS.ProcessEnv, packaged = false) {
  const dir = mkdtempSync(path.join(os.tmpdir(), 'campusflow-p008-config-'))
  tempDirs.push(dir)
  return loadConfig(env, dir, packaged)
}

test.afterEach(() => {
  for (const dir of tempDirs.splice(0)) rmSync(dir, { recursive: true, force: true })
})

test('首启初始化默认只在打包且非外置 PostgreSQL 时启用', () => {
  expect(configFor({}, false).bootstrap).toBe(false)
  expect(configFor({}, true).bootstrap).toBe(true)
  expect(configFor({ CAMPUSFLOW_MANAGED_POSTGRES: 'external' }, true).bootstrap).toBe(false)
  expect(configFor({ CAMPUSFLOW_BOOTSTRAP: '1' }, false).bootstrap).toBe(true)
  expect(configFor({ CAMPUSFLOW_BOOTSTRAP: '0' }, true).bootstrap).toBe(false)
})

test('测试桩模式永远跳过首启初始化', () => {
  expect(configFor({ CAMPUSFLOW_ELECTRON_STUB: '1', CAMPUSFLOW_BOOTSTRAP: '1' }, true).bootstrap).toBe(
    false,
  )
})

test('自动登录默认开启，可用 CAMPUSFLOW_AUTO_LOGIN=0 关闭', () => {
  expect(configFor({}).autoLogin).toBe(true)
  expect(configFor({ CAMPUSFLOW_AUTO_LOGIN: '0' }).autoLogin).toBe(false)
})
