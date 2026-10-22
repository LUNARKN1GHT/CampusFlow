import { mkdtempSync, rmSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'

import { expect, test } from '@playwright/test'

import { Logger } from '../src/logger'
import {
  checkAndApplyUpgrade,
  defaultArchiveName,
  exportArchive,
  formatBytes,
  importArchive,
  parseJsonOutput,
  resolveDefaultArchivePath,
  type DialogMessageOptions,
  type DialogPort,
  type MaintenanceContext,
} from '../src/maintenance'
import type { RunResult } from '../src/types'

const tempDirs: string[] = []

function tempLogger(): Logger {
  const dir = mkdtempSync(path.join(os.tmpdir(), 'campusflow-p010-log-'))
  tempDirs.push(dir)
  return new Logger(dir)
}

test.afterEach(() => {
  for (const dir of tempDirs.splice(0)) rmSync(dir, { recursive: true, force: true })
})

interface Call {
  command: string
  args: string[]
  timeoutMs: number
}

interface Harness {
  context: MaintenanceContext
  calls: Call[]
  messages: DialogMessageOptions[]
  suspended: number
  resumed: number
  relaunched: number
}

function harness(options: {
  savePath?: string
  openPaths?: string[]
  responses?: number[]
  results?: Record<string, RunResult | (() => RunResult)>
}): Harness {
  const calls: Call[] = []
  const messages: DialogMessageOptions[] = []
  const responses = [...(options.responses ?? [])]
  const state = { suspended: 0, resumed: 0, relaunched: 0 }
  const dialogs: DialogPort = {
    showSaveDialog: async () => ({ canceled: !options.savePath, filePath: options.savePath }),
    showOpenDialog: async () => ({
      canceled: !options.openPaths,
      filePaths: options.openPaths,
    }),
    showMessageBox: async (message) => {
      messages.push(message)
      return { response: responses.length > 0 ? responses.shift()! : 0 }
    },
  }
  const context: MaintenanceContext = {
    dialogs,
    logger: tempLogger(),
    suspendServices: async () => {
      state.suspended += 1
    },
    resumeServices: async () => {
      state.resumed += 1
    },
    relaunch: () => {
      state.relaunched += 1
    },
    defaultArchivePath: () => '/tmp/default.campusflow',
    run: async (command, args, timeoutMs) => {
      calls.push({ command, args, timeoutMs })
      const entry = options.results?.[command]
      if (!entry) return { code: 0, stdout: '{}', stderr: '' }
      return typeof entry === 'function' ? entry() : entry
    },
  }
  return {
    context,
    calls,
    messages,
    get suspended() {
      return state.suspended
    },
    get resumed() {
      return state.resumed
    },
    get relaunched() {
      return state.relaunched
    },
  }
}

test('归档文件名与大小摘要稳定', () => {
  const now = new Date(2026, 9, 21, 9, 5, 7)
  expect(defaultArchiveName(now)).toBe('campusflow-20261021-090507.campusflow')
  expect(resolveDefaultArchivePath('/docs', now)).toBe(
    path.join('/docs', 'campusflow-20261021-090507.campusflow'),
  )
  expect(formatBytes(0)).toBe('0 B')
  expect(formatBytes(512)).toBe('512 B')
  expect(formatBytes(2048)).toBe('2.0 KB')
  expect(formatBytes(5 * 1024 * 1024)).toBe('5.0 MB')
  expect(formatBytes('nope')).toBe('0 B')
})

test('JSON 输出只接受完整对象', () => {
  expect(parseJsonOutput('{"a":1}')).toEqual({ a: 1 })
  expect(parseJsonOutput('  {"a":1}\n')).toEqual({ a: 1 })
  expect(parseJsonOutput('log line\n{"a":1}')).toBeNull()
  expect(parseJsonOutput('[]')).toBeNull()
  expect(parseJsonOutput('')).toBeNull()
})

test('导出：保存对话框取消时不调用 sidecar', async () => {
  const h = harness({})
  expect(await exportArchive(h.context)).toBe('cancelled')
  expect(h.calls).toHaveLength(0)
})

test('导出：成功时展示路径、大小与 SHA-256', async () => {
  const h = harness({
    savePath: '/tmp/campusflow-manual',
    results: {
      export: {
        code: 0,
        stdout: JSON.stringify({
          path: '/tmp/campusflow-manual.campusflow',
          size: 213708,
          sha256: 'abc123',
          excluded_credentials: [{ table: 'calendar_connections' }],
        }),
        stderr: '',
      },
    },
  })
  expect(await exportArchive(h.context)).toBe('done')
  expect(h.calls).toEqual([
    { command: 'export', args: ['--output', '/tmp/campusflow-manual.campusflow'], timeoutMs: 600_000 },
  ])
  expect(h.messages.at(-1)?.type).toBe('info')
  const detail = h.messages.at(-1)?.detail ?? ''
  expect(detail).toContain('abc123')
  expect(detail).toContain('208.7 KB')
  expect(detail).toContain('已排除凭据：1 项')
})

test('导出：sidecar 失败时显示错误且不报告完成', async () => {
  const h = harness({
    savePath: '/tmp/export-fail.campusflow',
    results: {
      export: { code: 1, stdout: '', stderr: 'pg_dump: connection refused' },
    },
  })
  expect(await exportArchive(h.context)).toBe('failed')
  expect(h.messages.at(-1)?.type).toBe('error')
  expect(h.messages.at(-1)?.message).toBe('导出失败')
})

test('导入：检查后取消确认不会暂停服务或修改数据', async () => {
  const h = harness({
    openPaths: ['/tmp/backup.campusflow'],
    responses: [0],
    results: {
      import: {
        code: 0,
        stdout: JSON.stringify({
          confirm_token: 'token-1',
          counts: { tasks: 3 },
          material_count: 2,
        }),
        stderr: '',
      },
    },
  })
  expect(await importArchive(h.context)).toBe('cancelled')
  expect(h.calls).toHaveLength(1)
  expect(h.calls[0]!.args).toEqual(['/tmp/backup.campusflow', '--inspect'])
  expect(h.suspended).toBe(0)
  expect(h.relaunched).toBe(0)
})

test('导入：确认后暂停服务、携带确认令牌并重启应用', async () => {
  const h = harness({
    openPaths: ['/tmp/backup.campusflow'],
    responses: [1, 0],
    results: {
      import: (() => {
        let calls = 0
        return () => {
          calls += 1
          return calls === 1
            ? {
                code: 0,
                stdout: JSON.stringify({
                  confirm_token: 'token-1',
                  counts: { tasks: 3 },
                  material_count: 2,
                  material_bytes: 1024,
                  requires_reauthorization: ['calendar_connections'],
                }),
                stderr: '',
              }
            : {
                code: 0,
                stdout: JSON.stringify({
                  restored_counts: { tasks: 3 },
                  backup_archive: '/tmp/pre-import.tar',
                }),
                stderr: '',
              }
        }
      })(),
    },
  })
  expect(await importArchive(h.context)).toBe('done')
  expect(h.calls.map((call) => call.args[0])).toEqual([
    '/tmp/backup.campusflow',
    '/tmp/backup.campusflow',
  ])
  expect(h.calls[1]!.args).toEqual([
    '/tmp/backup.campusflow',
    '--replace',
    '--confirm-token',
    'token-1',
  ])
  expect(h.suspended).toBe(1)
  expect(h.resumed).toBe(0)
  expect(h.relaunched).toBe(1)
  const confirm = h.messages[0]!
  expect(confirm.buttons).toEqual(['取消', '导入并重启'])
  expect(confirm.detail).toContain('需要重新授权：calendar_connections')
  expect(h.messages.at(-1)?.detail).toContain('/tmp/pre-import.tar')
})

test('导入：失败时恢复服务且不重启', async () => {
  let calls = 0
  const h = harness({
    openPaths: ['/tmp/backup.campusflow'],
    responses: [1],
    results: {
      import: () => {
        calls += 1
        return calls === 1
          ? {
              code: 0,
              stdout: JSON.stringify({ confirm_token: 'token-1', material_count: 0 }),
              stderr: '',
            }
          : { code: 1, stdout: '', stderr: 'restore failed' }
      },
    },
  })
  expect(await importArchive(h.context)).toBe('failed')
  expect(h.suspended).toBe(1)
  expect(h.resumed).toBe(1)
  expect(h.relaunched).toBe(0)
  expect(h.messages.at(-1)?.message).toBe('导入失败')
})

test('导入：检查输出不是 JSON 时直接失败', async () => {
  const h = harness({
    openPaths: ['/tmp/broken.campusflow'],
    results: { import: { code: 0, stdout: 'not json', stderr: '' } },
  })
  expect(await importArchive(h.context)).toBe('failed')
  expect(h.messages.at(-1)?.message).toBe('无法读取归档')
  expect(h.suspended).toBe(0)
})

test('导入：归档缺少确认令牌时拒绝执行', async () => {
  const h = harness({
    openPaths: ['/tmp/no-token.campusflow'],
    results: { import: { code: 0, stdout: JSON.stringify({ material_count: 1 }), stderr: '' } },
  })
  expect(await importArchive(h.context)).toBe('failed')
  expect(h.messages.at(-1)?.message).toBe('归档缺少确认令牌')
  expect(h.suspended).toBe(0)
})

test('升级：已是最新时不暂停服务', async () => {
  const h = harness({
    results: {
      upgrade: {
        code: 0,
        stdout: JSON.stringify({
          state: 'up_to_date',
          database_revision: 'p011_upgrade',
          bundled_head: 'p011_upgrade',
        }),
        stderr: '',
      },
    },
  })
  expect(await checkAndApplyUpgrade(h.context)).toBe('done')
  expect(h.calls).toHaveLength(1)
  expect(h.calls[0]!.args).toEqual(['--check', '--json'])
  expect(h.suspended).toBe(0)
  expect(h.messages.at(-1)?.message).toBe('无需升级')
})

test('升级：数据库更新时拒绝降级', async () => {
  const h = harness({
    results: {
      upgrade: {
        code: 1,
        stdout: JSON.stringify({
          state: 'database_ahead',
          database_revision: 'future',
          bundled_head: 'p011_upgrade',
        }),
        stderr: '',
      },
    },
  })
  expect(await checkAndApplyUpgrade(h.context)).toBe('failed')
  expect(h.suspended).toBe(0)
  expect(h.messages.at(-1)?.message).toContain('更新版本')
})

test('升级：需要升级时暂停服务、备份并在成功后重启', async () => {
  let calls = 0
  const h = harness({
    responses: [1],
    results: {
      upgrade: () => {
        calls += 1
        return calls === 1
          ? {
              code: 1,
              stdout: JSON.stringify({
                state: 'upgrade_required',
                database_revision: 'old',
                bundled_head: 'new',
              }),
              stderr: '',
            }
          : {
              code: 0,
              stdout: JSON.stringify({
                state: 'up_to_date',
                database_revision: 'old',
                bundled_head: 'new',
                action: 'upgraded',
                backup: '/tmp/pre-upgrade.tar',
              }),
              stderr: '',
            }
      },
    },
  })
  expect(await checkAndApplyUpgrade(h.context)).toBe('done')
  expect(h.calls.map((call) => call.args)).toEqual([
    ['--check', '--json'],
    ['--check', '--apply', '--json'],
  ])
  expect(h.suspended).toBe(1)
  expect(h.resumed).toBe(0)
  expect(h.relaunched).toBe(1)
  expect(h.messages[0]!.detail).toContain('升级前会先做完整备份')
  expect(h.messages.at(-1)?.detail).toContain('/tmp/pre-upgrade.tar')
})

test('升级：应用失败时恢复服务', async () => {
  let calls = 0
  const h = harness({
    responses: [1],
    results: {
      upgrade: () => {
        calls += 1
        return calls === 1
          ? {
              code: 1,
              stdout: JSON.stringify({
                state: 'upgrade_required',
                database_revision: 'old',
                bundled_head: 'new',
              }),
              stderr: '',
            }
          : { code: 1, stdout: '', stderr: 'migration failed' }
      },
    },
  })
  expect(await checkAndApplyUpgrade(h.context)).toBe('failed')
  expect(h.suspended).toBe(1)
  expect(h.resumed).toBe(1)
  expect(h.relaunched).toBe(0)
})
