import { EventEmitter } from 'node:events'
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { PassThrough } from 'node:stream'

import { expect, test } from '@playwright/test'

import { loadConfig } from '../src/config'
import { Logger, redact } from '../src/logger'
import { Supervisor } from '../src/supervisor'
import type {
  ChildLike,
  RuntimeConfig,
  SpawnOptions,
  SupervisorDeps,
  SupervisorStatus,
} from '../src/types'

class FakeChild extends EventEmitter {
  pid: number
  exitCode: number | null = null
  readonly stdout = new PassThrough()
  readonly stderr = new PassThrough()
  readonly killSignals: NodeJS.Signals[] = []

  constructor(pid: number) {
    super()
    this.pid = pid
  }

  kill(signal: NodeJS.Signals = 'SIGTERM'): boolean {
    this.killSignals.push(signal)
    this.exitCode = 0
    queueMicrotask(() => this.emit('exit', 0, signal))
    return true
  }

  crash(code: number): void {
    this.exitCode = code
    this.emit('exit', code, null)
  }
}

interface Harness {
  supervisor: Supervisor
  config: RuntimeConfig
  logger: Logger
  calls: string[]
  kills: string[]
  children: FakeChild[]
  statuses: SupervisorStatus[]
}

const temporaryDirs: string[] = []

function baseConfig(overrides: Partial<RuntimeConfig> = {}): RuntimeConfig {
  const dir = mkdtempSync(path.join(os.tmpdir(), 'campusflow-p008-unit-'))
  temporaryDirs.push(dir)
  const loaded = loadConfig({}, dir)
  return {
    ...loaded,
    pgMode: 'skip',
    sidecarBin: '/bin/echo',
    stub: false,
    healthTimeoutMs: 500,
    healthPollIntervalMs: 2,
    workerGraceMs: 2,
    restartBaseDelayMs: 2,
    restartMaxDelayMs: 4,
    maxRestarts: 1,
    stopTimeoutMs: 200,
    ...overrides,
  }
}

function createHarness(
  options: {
    config?: Partial<RuntimeConfig>
    run?: SupervisorDeps['run']
    probeHttp?: SupervisorDeps['probeHttp']
    isProcessAlive?: SupervisorDeps['isProcessAlive']
  } = {},
): Harness {
  const config = baseConfig(options.config)
  const logger = new Logger(config.logDir)
  const calls: string[] = []
  const kills: string[] = []
  const children: FakeChild[] = []
  const statuses: SupervisorStatus[] = []
  let nextPid = 900_000

  const deps: Partial<SupervisorDeps> = {
    spawn: (command: string, args: string[], _options: SpawnOptions) => {
      const role = args[0] === 'worker' ? 'worker' : 'api'
      calls.push(`spawn:${args[0]}`)
      const child = new FakeChild((nextPid += 1))
      child.on('exit', () => kills.push(role))
      children.push(child)
      queueMicrotask(() => child.emit('spawn'))
      return child as unknown as ChildLike
    },
    run: async (command: string, args: string[]) => {
      if (options.run) {
        return options.run(command, args, {
          cwd: config.dataDir,
          env: {},
          timeoutMs: 1_000,
        })
      }
      calls.push(`run:${args[0]}`)
      return { code: 0, stdout: '', stderr: '' }
    },
    probeHttp: options.probeHttp ?? (async () => true),
    findFreePort: async () => 43_210,
    sleep: (ms: number) => new Promise((resolve) => setTimeout(resolve, ms)),
    now: () => Date.now(),
    isProcessAlive: options.isProcessAlive ?? (() => false),
  }

  const supervisor = new Supervisor({ config, logger, deps })
  supervisor.on('status', (status) => statuses.push(status))
  return { supervisor, config, logger, calls, kills, children, statuses }
}

async function eventually(
  predicate: () => boolean | Promise<boolean>,
  timeoutMs = 3_000,
): Promise<void> {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    if (await predicate()) return
    await new Promise((resolve) => setTimeout(resolve, 5))
  }
  throw new Error('等待条件超时')
}

test.afterEach(() => {
  for (const dir of temporaryDirs.splice(0)) {
    rmSync(dir, { recursive: true, force: true })
  }
})

test('按 PostgreSQL → migrate → API → Worker 顺序启动，并按 Worker → API 逆序停止', async () => {
  const harness = createHarness()
  await harness.supervisor.start()

  const reportedStages = harness.statuses
    .map((status) => status.stage)
    .filter((stage, index, all) => all.indexOf(stage) === index)
  expect(reportedStages).toEqual(['postgres', 'migrate', 'api', 'worker', 'ready'])
  expect(harness.calls).toEqual(['run:migrate', 'spawn:serve', 'spawn:worker'])
  expect(harness.supervisor.getStatus().phase).toBe('ready')
  expect(harness.supervisor.getStatus().port).toBe(43_210)

  await harness.supervisor.stop()
  expect(harness.kills).toEqual(['worker', 'api'])
  expect(harness.supervisor.getStatus().phase).toBe('stopped')
  cleanupHarness()
})

test('迁移失败时停在 migrate 阶段并给出日志路径，不启动 API', async () => {
  const harness = createHarness({
    run: async (_command, args) => {
      if (args[0] === 'migrate') {
        return { code: 2, stdout: '', stderr: '迁移脚本失败' }
      }
      return { code: 0, stdout: '', stderr: '' }
    },
  })

  await expect(harness.supervisor.start()).rejects.toThrow('数据库迁移阶段失败')
  const status = harness.supervisor.getStatus()
  expect(status.phase).toBe('failed')
  expect(status.failure?.stage).toBe('migrate')
  expect(status.failure?.message).toContain('退出码 2')
  expect(status.failure?.logPath).toContain('main.log')
  expect(status.failure?.logTail.some((line) => line.includes('阶段开始：migrate'))).toBe(true)
  expect(harness.calls).toEqual([])
  cleanupHarness()
})

test('API 异常退出后按退避重启，连续失败后进入 failed 并回收其它子进程', async () => {
  const harness = createHarness({ config: { maxRestarts: 1 } })
  await harness.supervisor.start()
  expect(harness.children).toHaveLength(2)

  harness.children[0]!.crash(1)
  await eventually(() => harness.children.length === 3)
  await eventually(
    () =>
      harness.statuses.filter((status) => status.message === '全部服务已就绪，正在加载界面…').length >= 2,
  )
  expect(harness.calls.filter((call) => call === 'spawn:serve')).toHaveLength(2)

  harness.children[2]!.crash(1)
  await eventually(() => harness.supervisor.getStatus().phase === 'failed')
  const status = harness.supervisor.getStatus()
  expect(status.failure?.stage).toBe('api')
  expect(status.failure?.message).toContain('连续异常退出')
  await eventually(() => harness.kills.includes('worker'))
  cleanupHarness()
})

test('Worker 异常退出同样会重启', async () => {
  const harness = createHarness({ config: { maxRestarts: 2 } })
  await harness.supervisor.start()
  harness.children[1]!.crash(78)
  await eventually(() => harness.children.length === 3)
  await eventually(
    () =>
      harness.statuses.filter((status) => status.message === '全部服务已就绪，正在加载界面…').length >= 2,
  )
  expect(harness.calls.filter((call) => call === 'spawn:worker')).toHaveLength(2)
  await harness.supervisor.stop()
  cleanupHarness()
})

test('PostgreSQL 进程意外退出会停止整套服务', async () => {
  let pgAlive = true
  const harness = createHarness({
    config: { pgMode: 'managed', pgBin: '/fake/pg', pgReadyTimeoutMs: 1_000, pgPollIntervalMs: 5 },
    run: async () => ({ code: 0, stdout: '', stderr: '' }),
    isProcessAlive: () => pgAlive,
  })
  mkdirSync(harness.config.pgDataDir, { recursive: true })
  writeFileSync(path.join(harness.config.pgDataDir, 'PG_VERSION'), '17\n')
  writeFileSync(path.join(harness.config.pgDataDir, 'postmaster.pid'), '999999\n')

  await harness.supervisor.start()
  expect(harness.supervisor.getStatus().phase).toBe('ready')

  pgAlive = false
  await eventually(() => harness.supervisor.getStatus().phase === 'failed', 5_000)
  const status = harness.supervisor.getStatus()
  expect(status.failure?.stage).toBe('postgres')
  expect(status.failure?.message).toContain('PostgreSQL')
  await eventually(() => harness.kills.includes('worker') && harness.kills.includes('api'))
  cleanupHarness()
})

test('日志脱敏不保留口令、令牌或连接串凭据', () => {
  const redacted = redact(
    'password=campusflow-dev token:abc123 https://student:secret@127.0.0.1/db Authorization: Bearer xyz',
  )
  expect(redacted).not.toContain('campusflow-dev')
  expect(redacted).not.toContain('abc123')
  expect(redacted).not.toContain('secret@')
  expect(redacted).not.toContain('Bearer xyz')
  expect(redacted).not.toContain('xyz')
})

function cleanupHarness(): void {
  for (const dir of temporaryDirs.splice(0)) {
    rmSync(dir, { recursive: true, force: true })
  }
}
