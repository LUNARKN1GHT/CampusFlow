import { EventEmitter } from 'node:events'
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
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
  runCalls: RunCall[]
  spawnCalls: SpawnCall[]
}

interface RunCall {
  command: string
  args: string[]
  env: NodeJS.ProcessEnv
}

interface SpawnCall {
  command: string
  args: string[]
  env: NodeJS.ProcessEnv
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
  const runCalls: RunCall[] = []
  const spawnCalls: SpawnCall[] = []
  let nextPid = 900_000

  const deps: Partial<SupervisorDeps> = {
    spawn: (command: string, args: string[], spawnOptions: SpawnOptions) => {
      const role = args[0] === 'worker' ? 'worker' : 'api'
      calls.push(`spawn:${args[0]}`)
      spawnCalls.push({ command, args: [...args], env: { ...spawnOptions.env } })
      const child = new FakeChild((nextPid += 1))
      child.on('exit', () => kills.push(role))
      children.push(child)
      queueMicrotask(() => child.emit('spawn'))
      return child as unknown as ChildLike
    },
    run: async (
      command: string,
      args: string[],
      runOptions: { cwd: string; env: NodeJS.ProcessEnv; timeoutMs: number },
    ) => {
      runCalls.push({ command, args: [...args], env: { ...runOptions.env } })
      // Windows 停止服务走 taskkill；模拟它结束对应子进程，让 exit 与顺序断言有效。
      const pidFlag = args.indexOf('/pid')
      if (command === 'taskkill' && pidFlag >= 0) {
        const target = children.find((child) => child.pid === Number(args[pidFlag + 1]))
        target?.kill(args.includes('/F') ? 'SIGKILL' : 'SIGTERM')
      }
      if (options.run) {
        return options.run(command, args, runOptions)
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
  return { supervisor, config, logger, calls, kills, children, statuses, runCalls, spawnCalls }
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

test('bootstrap 成功时跳过 initdb，先过升级闸门再按 bootstrap → migrate → API → Worker 启动', async () => {
  const databaseUrl =
    'postgresql+psycopg://campusflow:db-secret@127.0.0.1:59945/campusflow'
  const harness = createHarness({
    config: { bootstrap: true, pgMode: 'managed', pgBin: '/fake/pg' },
    run: async (_command, args) => {
      if (args[0] === 'bootstrap') {
        return {
          code: 0,
          stdout: JSON.stringify({
            ready: true,
            status: 'initialized',
            initialized: true,
            data_dir: '/tmp/fake-data',
            database_url: databaseUrl,
            local_username: 'campusflow',
            local_password: 'local-login-token',
            schema_head: 'k001_external_calendar',
            secret_backend: 'file',
            warnings: ['凭据回退到 0600 文件'],
            messages: ['数据库已就绪'],
            started_postgres: true,
            postgres_running: true,
          }),
          stderr: '',
        }
      }
      if (args[0] === 'upgrade') {
        return {
          code: 0,
          stdout: JSON.stringify({
            state: 'up_to_date',
            database_revision: 'k001_external_calendar',
            bundled_head: 'k001_external_calendar',
            action: null,
          }),
          stderr: '',
        }
      }
      return { code: 0, stdout: '', stderr: '' }
    },
  })

  await harness.supervisor.start()

  const reportedStages = harness.statuses
    .map((status) => status.stage)
    .filter((stage, index, all) => all.indexOf(stage) === index)
  expect(reportedStages).toEqual(['bootstrap', 'migrate', 'api', 'worker', 'ready'])
  expect(harness.supervisor.getStatus().phase).toBe('ready')
  expect(harness.supervisor.getBootstrapLogin()).toEqual({
    username: 'campusflow',
    password: 'local-login-token',
  })

  // 只执行 bootstrap 与升级检查：不再调用 initdb、pg_ctl start、createdb 或 migrate。
  expect(harness.runCalls.map((call) => call.args[0])).toEqual(['bootstrap', 'upgrade'])
  expect(harness.runCalls[1]!.args).toEqual(['upgrade', '--check', '--apply', '--json'])
  expect(harness.runCalls[1]!.env.CAMPUSFLOW_DATABASE_URL).toBe(databaseUrl)
  expect(harness.runCalls[1]!.env.CAMPUSFLOW_LOCAL_PASSWORD).toBeUndefined()
  expect(harness.spawnCalls).toHaveLength(2)
  // bootstrap 需要空数据目录：不能提前在数据目录下创建 materials。
  expect(existsSync(harness.config.dataDir)).toBe(true)
  expect(existsSync(harness.config.storageDir)).toBe(false)

  for (const call of harness.spawnCalls) {
    expect(call.env.CAMPUSFLOW_DATABASE_URL).toBe(databaseUrl)
    expect(call.env.CAMPUSFLOW_LOCAL_USERNAME).toBe('campusflow')
    expect(call.env.CAMPUSFLOW_LOCAL_PASSWORD).toBe('local-login-token')
    expect(call.env.CAMPUSFLOW_DEPLOYMENT_MODE).toBe('desktop')
    expect(call.env.CAMPUSFLOW_DATA_DIR).toBe(harness.config.dataDir)
  }
  const bootstrapEnv = harness.runCalls[0]!.env
  expect(bootstrapEnv.CAMPUSFLOW_LOG_DIR).toBe(harness.config.logDir)
  expect(bootstrapEnv.CAMPUSFLOW_PG_BIN).toBe('/fake/pg')
  expect(bootstrapEnv.CAMPUSFLOW_PG_PORT).toBe(String(harness.config.pgPort))
  expect(bootstrapEnv.CAMPUSFLOW_API_PORT).toBe('43210')
  expect(bootstrapEnv.CAMPUSFLOW_LOCAL_PASSWORD).toBeUndefined()

  await harness.supervisor.stop()
  expect(harness.kills).toEqual(['worker', 'api'])
  const stopCall = harness.runCalls.find((call) => call.args.includes('stop'))
  const pgCtlName = process.platform === 'win32' ? 'pg_ctl.exe' : 'pg_ctl'
  expect(stopCall?.command).toBe(path.join('/fake/pg', pgCtlName))
  expect(stopCall?.args).toEqual([
    '-D',
    harness.config.pgDataDir,
    '-m',
    'fast',
    '-w',
    'stop',
  ])
  expect(harness.supervisor.getStatus().phase).toBe('stopped')
  // 退出后不再保留凭据。
  expect(harness.supervisor.getBootstrapLogin()).toBeNull()
  cleanupHarness()
})

test('维护操作暂停 API 与 Worker、保留 PostgreSQL，并可恢复服务', async () => {
  const databaseUrl = 'postgresql+psycopg://campusflow:db-secret@127.0.0.1:59945/campusflow'
  const harness = createHarness({
    config: { bootstrap: true, pgMode: 'managed', pgBin: '/fake/pg' },
    run: async (_command, args) => {
      if (args[0] === 'bootstrap') {
        return {
          code: 0,
          stdout: JSON.stringify({
            ready: true,
            status: 'initialized',
            initialized: true,
            database_url: databaseUrl,
            local_username: 'campusflow',
            local_password: 'local-login-token',
            schema_head: 'p011_upgrade',
            secret_backend: 'file',
            warnings: [],
            messages: [],
            started_postgres: true,
            postgres_running: true,
          }),
          stderr: '',
        }
      }
      return { code: 0, stdout: '{"ok":true}', stderr: '' }
    },
  })
  await harness.supervisor.start()
  expect(harness.supervisor.getStatus().phase).toBe('ready')

  await harness.supervisor.suspendServices()
  expect(harness.kills).toEqual(['worker', 'api'])
  expect(harness.supervisor.getStatus().phase).toBe('stopping')
  // 暂停只回收 API 与 Worker，PostgreSQL 保持运行。
  expect(harness.runCalls.some((call) => call.args.includes('stop'))).toBe(false)

  const result = await harness.supervisor.runMaintenance(
    'export',
    ['--output', '/tmp/archive.campusflow'],
    1_000,
  )
  expect(result.code).toBe(0)
  const exportCall = harness.runCalls.at(-1)!
  expect(exportCall.args).toEqual(['export', '--output', '/tmp/archive.campusflow'])
  expect(exportCall.env.CAMPUSFLOW_DATABASE_URL).toBe(databaseUrl)
  // 维护命令不需要本机登录凭据或 API 端口。
  expect(exportCall.env.CAMPUSFLOW_LOCAL_USERNAME).toBeUndefined()
  expect(exportCall.env.CAMPUSFLOW_LOCAL_PASSWORD).toBeUndefined()
  expect(exportCall.env.CAMPUSFLOW_API_PORT).toBeUndefined()

  await harness.supervisor.resumeServices()
  expect(harness.supervisor.getStatus().phase).toBe('ready')
  expect(harness.spawnCalls.at(-1)!.args[0]).toBe('worker')

  await harness.supervisor.stop()
  expect(harness.supervisor.getStatus().phase).toBe('stopped')
  cleanupHarness()
})

test('启动时数据库比安装包新则拒绝启动且不降级', async () => {
  const harness = createHarness({
    config: { bootstrap: true, pgMode: 'managed', pgBin: '/fake/pg' },
    run: async (_command, args) => {
      if (args[0] === 'bootstrap') {
        return {
          code: 0,
          stdout: JSON.stringify({
            ready: true,
            status: 'initialized',
            initialized: false,
            database_url: 'postgresql+psycopg://campusflow:db@127.0.0.1:59945/campusflow',
            local_username: 'campusflow',
            local_password: 'local-login-token',
            schema_head: 'future',
            secret_backend: 'file',
            warnings: [],
            messages: [],
            started_postgres: true,
            postgres_running: true,
          }),
          stderr: '',
        }
      }
      return {
        code: 1,
        stdout: JSON.stringify({
          state: 'database_ahead',
          database_revision: 'future',
          bundled_head: 'p011_upgrade',
        }),
        stderr: '',
      }
    },
  })

  await expect(harness.supervisor.start()).rejects.toThrow(/不会降级/)
  expect(harness.supervisor.getStatus().phase).toBe('failed')
  expect(harness.supervisor.getStatus().stage).toBe('migrate')
  expect(harness.spawnCalls).toHaveLength(0)
  expect(harness.supervisor.getStatus().failure?.message).toContain('future')
  cleanupHarness()
})

test('bootstrap 退出码非零时停在 bootstrap 阶段，不启动 API 且错误信息包含诊断目录', async () => {
  const harness = createHarness({
    config: { bootstrap: true, pgBin: '/fake/pg' },
    run: async (_command, args) => {
      if (args[0] === 'bootstrap') {
        return {
          code: 1,
          stdout: '',
          stderr: '初始化失败：无法创建数据库（诊断日志目录：/tmp/fake-data.failed-20261021/logs）',
        }
      }
      return { code: 0, stdout: '', stderr: '' }
    },
  })

  await expect(harness.supervisor.start()).rejects.toThrow('首启初始化阶段失败')
  const status = harness.supervisor.getStatus()
  expect(status.phase).toBe('failed')
  expect(status.failure?.stage).toBe('bootstrap')
  expect(status.failure?.message).toContain('退出码 1')
  expect(status.failure?.message).toContain('初始化失败')
  expect(status.failure?.message).toContain(path.join(harness.config.dataDir, 'logs'))
  expect(status.failure?.logTail.some((line) => line.includes('阶段开始：bootstrap'))).toBe(true)
  expect(harness.spawnCalls).toHaveLength(0)
  expect(harness.calls).toEqual([])
  expect(harness.supervisor.getBootstrapLogin()).toBeNull()
  cleanupHarness()
})

test('bootstrap 返回 ready=false 时同样失败，且不把口令写入日志', async () => {
  const harness = createHarness({
    config: { bootstrap: true, pgBin: '/fake/pg' },
    run: async (_command, args) => {
      if (args[0] === 'bootstrap') {
        return {
          code: 0,
          stdout: JSON.stringify({
            ready: false,
            status: 'partially_initialized',
            initialized: true,
            data_dir: '/tmp/fake-data',
            database_url: null,
            local_username: null,
            local_password: 'should-never-be-logged',
            schema_head: 'k001_external_calendar',
            secret_backend: 'file',
            warnings: [],
            messages: ['数据库口令缺失，不会使用默认口令'],
            started_postgres: false,
            postgres_running: false,
          }),
          stderr: '',
        }
      }
      return { code: 0, stdout: '', stderr: '' }
    },
  })

  await expect(harness.supervisor.start()).rejects.toThrow('首启初始化阶段失败')
  const status = harness.supervisor.getStatus()
  expect(status.failure?.stage).toBe('bootstrap')
  expect(status.failure?.message).toContain('partially_initialized')
  expect(status.failure?.message).toContain('不会使用默认口令')
  expect(harness.spawnCalls).toHaveLength(0)
  const log = readFileSync(harness.logger.logPath, 'utf8')
  expect(log).not.toContain('should-never-be-logged')
  cleanupHarness()
})

test('测试桩模式下即使显式要求也跳过 bootstrap', async () => {
  const config = baseConfig({ stub: true, bootstrap: true })
  const harness = createHarness({ config })
  await harness.supervisor.start()
  expect(harness.runCalls.filter((call) => call.args[0] === 'bootstrap')).toHaveLength(0)
  expect(harness.supervisor.getBootstrapLogin()).toBeNull()
  await harness.supervisor.stop()
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
