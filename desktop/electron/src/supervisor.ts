import { EventEmitter } from 'node:events'
import { existsSync, mkdirSync, readFileSync } from 'node:fs'
import path from 'node:path'

import { defaultDeps } from './runtime'
import { redact, type Logger } from './logger'
import type {
  ChildLike,
  RunResult,
  RuntimeConfig,
  StageName,
  SupervisorDeps,
  SupervisorStatus,
} from './types'

const STAGE_LABELS: Record<StageName, string> = {
  postgres: 'PostgreSQL',
  migrate: '数据库迁移',
  api: 'API 服务',
  worker: '后台 Worker',
}

const STAGE_MESSAGES: Record<StageName, string> = {
  postgres: '正在启动 PostgreSQL…',
  migrate: '正在执行数据库迁移…',
  api: '正在启动 API 服务…',
  worker: '正在启动后台 Worker…',
}

const READY_MESSAGE = '全部服务已就绪，正在加载界面…'

interface ManagedChild {
  name: 'api' | 'worker'
  child: ChildLike
  startedAt: number
}

export interface SupervisorOptions {
  config: RuntimeConfig
  logger: Logger
  deps?: Partial<SupervisorDeps>
}

function toolPath(binDir: string, name: string): string {
  return path.join(binDir, process.platform === 'win32' ? `${name}.exe` : name)
}

function describeError(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

function brief(text: string, limit = 240): string {
  const cleaned = redact(text).replace(/\s+/g, ' ').trim()
  return cleaned.length > limit ? `…${cleaned.slice(-limit)}` : cleaned
}

function parseJsonObject(text: string): Record<string, unknown> | null {
  const start = text.indexOf('{')
  const end = text.lastIndexOf('}')
  if (start < 0 || end <= start) return null
  try {
    const value = JSON.parse(text.slice(start, end + 1)) as unknown
    return value && typeof value === 'object' ? (value as Record<string, unknown>) : null
  } catch {
    return null
  }
}

/**
 * 进程监管器：严格按 PostgreSQL → migrate → API → Worker 启动，
 * 就绪后发出 ready；API／Worker 异常退出按指数退避重启，PostgreSQL
 * 退出直接停止整套服务。所有子进程都记录 PID，退出时逆序回收。
 */
export class Supervisor extends EventEmitter {
  private readonly config: RuntimeConfig
  private readonly logger: Logger
  private readonly deps: SupervisorDeps
  private status: SupervisorStatus
  private readonly managed = new Map<'api' | 'worker', ManagedChild>()
  private readonly restartCounts: Record<'api' | 'worker', number> = { api: 0, worker: 0 }
  private readonly restarting = new Set<'api' | 'worker'>()
  private readonly resetTimers = new Set<NodeJS.Timeout>()
  private startPromise: Promise<void> | null = null
  private stopPromise: Promise<void> | null = null
  private stopping = false
  private failing = false
  private stubFailureConsumed = false
  private pgManaged = false
  private pgMonitor: NodeJS.Timeout | null = null
  private pgPid: number | null = null
  private port: number | undefined
  private currentStage: StageName = 'postgres'

  constructor(options: SupervisorOptions) {
    super()
    this.config = options.config
    this.logger = options.logger
    this.deps = { ...defaultDeps, ...options.deps }
    this.status = {
      phase: 'idle',
      stage: 'idle',
      message: '等待启动',
      updatedAt: new Date(this.deps.now()).toISOString(),
    }
  }

  getStatus(): SupervisorStatus {
    return {
      ...this.status,
      ...(this.status.failure ? { failure: { ...this.status.failure } } : {}),
    }
  }

  getPort(): number | undefined {
    return this.port
  }

  start(): Promise<void> {
    if (this.startPromise) return this.startPromise
    this.startPromise = this.runStartup().finally(() => {
      this.startPromise = null
    })
    return this.startPromise
  }

  retry(): Promise<void> {
    if (this.startPromise) return this.startPromise
    this.restartCounts.api = 0
    this.restartCounts.worker = 0
    return this.start()
  }

  async stop(): Promise<void> {
    if (this.stopPromise) return this.stopPromise
    this.stopping = true
    this.setStatus({ phase: 'stopping', message: '正在停止服务，请稍候…' })
    this.stopPromise = (async () => {
      await this.stopChildren()
      this.setStatus({ phase: 'stopped', stage: 'idle', message: '服务已停止' })
    })()
    return this.stopPromise
  }

  private async runStartup(): Promise<void> {
    this.ensureDirectories()
    this.stopping = false
    this.failing = false
    this.pgManaged = false
    this.stopPromise = null
    this.port = this.config.apiPort > 0 ? this.config.apiPort : await this.deps.findFreePort()
    this.currentStage = 'postgres'
    this.setStatus({
      phase: 'starting',
      stage: 'postgres',
      message: '正在准备桌面运行环境…',
      port: this.port,
    })

    try {
      await this.stage('postgres', () => this.startPostgres())
      await this.stage('migrate', () => this.runMigrate())
      await this.stage('api', () => this.startApi())
      await this.stage('worker', () => this.startWorker())
      this.setStatus({ phase: 'ready', stage: 'ready', message: READY_MESSAGE, port: this.port })
      this.logger.info(`启动完成：所有阶段就绪，端口 ${this.port}`)
    } catch (error) {
      this.failing = true
      await this.stopChildren()
      this.failing = false
      if (this.stopping) {
        this.setStatus({ phase: 'stopped', stage: 'idle', message: '服务已停止' })
        return
      }
      const message = describeError(error)
      const failure = this.buildFailure(this.currentStage, message)
      this.setStatus({
        phase: 'failed',
        stage: this.currentStage,
        message: `${STAGE_LABELS[this.currentStage]}阶段失败：${message}`,
        failure,
        port: this.port,
      })
      this.logger.error(`启动失败：阶段 ${this.currentStage}，${message}`)
      throw new Error(`${STAGE_LABELS[this.currentStage]}阶段失败：${message}`)
    }
  }

  private async stage(name: StageName, action: () => Promise<void>): Promise<void> {
    if (this.stopping) throw new Error('启动已取消')
    this.currentStage = name
    this.setStatus({ phase: 'starting', stage: name, message: STAGE_MESSAGES[name] })
    this.logger.info(`阶段开始：${name}`)
    if (this.config.stub) {
      await this.delay(this.config.stubDelayMs)
      this.injectStubFailure(name)
    }
    await action()
    if (this.stopping) throw new Error('启动已取消')
    this.logger.info(`阶段完成：${name}`)
  }

  private injectStubFailure(stage: StageName): void {
    if (!this.config.stub || this.config.stubFailStage !== stage) return
    if (!this.config.stubFailAlways && this.stubFailureConsumed) return
    this.stubFailureConsumed = true
    throw new Error(`测试桩模拟${STAGE_LABELS[stage]}阶段失败`)
  }

  private setStatus(patch: Partial<SupervisorStatus>): void {
    this.status = {
      ...this.status,
      ...patch,
      updatedAt: new Date(this.deps.now()).toISOString(),
    }
    const shown = this.status.failure
      ? `${this.status.message}（失败阶段：${this.status.failure.stage}）`
      : this.status.message
    this.logger.info(`状态：phase=${this.status.phase} stage=${this.status.stage} ${shown}`)
    this.emit('status', this.getStatus())
  }

  private ensureDirectories(): void {
    for (const dir of [
      this.config.dataDir,
      this.config.storageDir,
      this.config.backupDir,
      this.config.logDir,
      this.config.runDir,
    ]) {
      mkdirSync(dir, { recursive: true })
    }
  }

  private childEnv(): NodeJS.ProcessEnv {
    return {
      ...process.env,
      CAMPUSFLOW_DESKTOP: '1',
      CAMPUSFLOW_DATA_DIR: this.config.dataDir,
      CAMPUSFLOW_STORAGE_DIR: this.config.storageDir,
      CAMPUSFLOW_API_PORT: String(this.port ?? 0),
      ...(this.config.staticDir ? { CAMPUSFLOW_STATIC_DIR: this.config.staticDir } : {}),
      CAMPUSFLOW_PG_PORT: String(this.config.pgPort),
      CAMPUSFLOW_PG_DATABASE: this.config.pgDatabase,
      ...(this.config.pgMode === 'managed' && !process.env.CAMPUSFLOW_DATABASE_URL
        ? {
            CAMPUSFLOW_DATABASE_URL: `postgresql+psycopg://${encodeURIComponent(
              this.config.pgUser,
            )}@${this.config.host}:${this.config.pgPort}/${encodeURIComponent(
              this.config.pgDatabase,
            )}`,
          }
        : {}),
    }
  }

  private sidecarCommand(
    subcommand: string,
    extra: string[],
  ): { command: string; args: string[]; cwd: string } {
    if (this.config.sidecarBin) {
      return {
        command: this.config.sidecarBin,
        args: [subcommand, ...extra],
        cwd: this.config.dataDir,
      }
    }
    return {
      command: this.config.pythonCommand,
      args: ['-m', this.config.sidecarModule, subcommand, ...extra],
      cwd: this.config.backendDir,
    }
  }

  private stubCommand(
    role: 'api' | 'worker',
    extra: string[],
  ): { command: string; args: string[]; cwd: string } {
    const script = path.join(__dirname, 'stub-sidecar.js')
    return {
      command: process.execPath,
      args: [
        script,
        '--role',
        role,
        '--pid-file',
        path.join(this.config.runDir, `stub-${role}.pid`),
        '--run-dir',
        this.config.runDir,
        ...extra,
      ],
      cwd: this.config.runDir,
    }
  }

  private async runSidecar(
    subcommand: string,
    extra: string[],
    timeoutMs: number,
  ): Promise<RunResult> {
    const command = this.sidecarCommand(subcommand, extra)
    return this.deps.run(command.command, command.args, {
      cwd: command.cwd,
      env: this.childEnv(),
      timeoutMs,
    })
  }

  private missingSidecarHint(result: RunResult): string {
    if (result.errorCode === 'ENOENT') {
      return '未找到 sidecar（P006）：请设置 CAMPUSFLOW_SIDECAR_BIN，或配置 CAMPUSFLOW_PYTHON 与 CAMPUSFLOW_BACKEND_DIR 使用开发回退命令。'
    }
    return brief(result.stderr) ? `输出：${brief(result.stderr)}` : ''
  }

  private async startPostgres(): Promise<void> {
    if (this.config.stub) {
      this.logger.info('测试桩：跳过真实 PostgreSQL，仅推进启动阶段')
      return
    }
    if (this.config.pgMode === 'skip') {
      this.logger.warn('CAMPUSFLOW_PG_MODE=skip：不启动数据库，交由迁移阶段验证连接')
      return
    }
    if (this.config.pgMode === 'external') {
      this.logger.info('外部 PostgreSQL 模式：不接管数据库进程，仅探测连接')
      await this.waitForSql()
      return
    }
    this.logger.info(`启动内置 PostgreSQL：端口 ${this.config.pgPort}`)
    await this.startManagedPostgres()
    await this.waitForSql()
  }

  private async startManagedPostgres(): Promise<void> {
    if (!this.config.pgBin) {
      throw new Error('CAMPUSFLOW_PG_MODE=managed 但未配置 CAMPUSFLOW_PG_BIN')
    }
    mkdirSync(this.config.pgDataDir, { recursive: true })
    const env = this.childEnv()
    const dataFile = path.join(this.config.pgDataDir, 'PG_VERSION')
    if (!existsSync(dataFile)) {
      this.logger.info('初始化 PostgreSQL 数据目录…')
      // 仅监听 loopback；随机数据库口令、建库与扩展由 P007 首启初始化接管，
      // 这里先用 trust 完成最小可启动闭环，不把该默认值当作生产凭据方案。
      const result = await this.deps.run(
        toolPath(this.config.pgBin, 'initdb'),
        [
          '-D',
          this.config.pgDataDir,
          '-U',
          this.config.pgUser,
          '--auth=trust',
          '--encoding=UTF8',
          '--no-locale',
        ],
        { cwd: this.config.dataDir, env, timeoutMs: 180_000 },
      )
      if (result.code !== 0) {
        throw new Error(`初始化 PostgreSQL 失败（退出码 ${result.code}）：${brief(result.stderr)}`)
      }
    }
    const pgLog = path.join(this.config.logDir, 'postgres.log')
    const serverOptions = `-p ${this.config.pgPort} -h 127.0.0.1 -k ${this.config.runDir}`
    const result = await this.deps.run(
      toolPath(this.config.pgBin, 'pg_ctl'),
      [
        '-D',
        this.config.pgDataDir,
        '-l',
        pgLog,
        '-o',
        serverOptions,
        '-w',
        '-t',
        '30',
        'start',
      ],
      { cwd: this.config.dataDir, env, timeoutMs: 60_000 },
    )
    if (result.code !== 0) {
      throw new Error(
        `启动 PostgreSQL 失败（退出码 ${result.code}）：${brief(result.stderr)}；日志：${pgLog}`,
      )
    }
    this.pgManaged = true
    this.startPostgresMonitor()
    this.logger.info('PostgreSQL 进程已启动')
  }

  private async waitForSql(): Promise<void> {
    const deadline = this.deps.now() + this.config.pgReadyTimeoutMs
    while (this.deps.now() < deadline) {
      if (this.stopping) throw new Error('启动已取消')
      const state = await this.probeSql()
      if (state === 'ready') {
        this.logger.info('PostgreSQL 就绪检查通过')
        return
      }
      if (state === 'unverified') {
        this.logger.warn(
          '未找到 pg_isready 或可用的 sidecar doctor，跳过 SQL 就绪探测，交由迁移阶段验证',
        )
        return
      }
      await this.delay(this.config.pgPollIntervalMs)
    }
    throw new Error(
      `PostgreSQL 在 ${Math.round(this.config.pgReadyTimeoutMs / 1000)} 秒内未就绪`,
    )
  }

  private async probeSql(): Promise<'ready' | 'pending' | 'unverified'> {
    const pgIsReady = this.config.pgBin
      ? toolPath(this.config.pgBin, 'pg_isready')
      : 'pg_isready'
    const result = await this.deps.run(
      pgIsReady,
      ['-h', this.config.host, '-p', String(this.config.pgPort)],
      { cwd: this.config.dataDir, env: this.childEnv(), timeoutMs: 3_000 },
    )
    if (result.errorCode === 'ENOENT') {
      return this.probeSqlWithDoctor()
    }
    return result.code === 0 ? 'ready' : 'pending'
  }

  private async probeSqlWithDoctor(): Promise<'ready' | 'pending' | 'unverified'> {
    const result = await this.runSidecar('doctor', [], 30_000)
    if (result.errorCode === 'ENOENT') return 'unverified'
    const report = parseJsonObject(result.stdout)
    if (!report) return 'unverified'
    const database = report.database as { ok?: unknown } | undefined
    return database?.ok === true ? 'ready' : 'pending'
  }

  private async runMigrate(): Promise<void> {
    if (this.config.stub) {
      this.logger.info('测试桩：跳过真实数据库迁移')
      return
    }
    const result = await this.runSidecar('migrate', [], 180_000)
    if (result.code !== 0) {
      const hint = this.missingSidecarHint(result)
      throw new Error(
        `数据库迁移失败（退出码 ${result.code}）${hint ? `；${hint}` : ''}`,
      )
    }
    this.logger.info('数据库迁移完成')
  }

  private async startApi(): Promise<void> {
    if (this.config.stub) {
      const stub = this.stubCommand('api', ['--port', String(this.port ?? 0)])
      await this.launchService('api', stub.command, stub.args, stub.cwd)
      return
    }
    const command = this.sidecarCommand('serve', [
      '--host',
      this.config.host,
      '--port',
      String(this.port ?? 0),
    ])
    await this.launchService('api', command.command, command.args, command.cwd)
  }

  private async startWorker(): Promise<void> {
    if (this.config.stub) {
      const stub = this.stubCommand('worker', [])
      await this.launchService('worker', stub.command, stub.args, stub.cwd)
      return
    }
    const command = this.sidecarCommand('worker', [])
    await this.launchService('worker', command.command, command.args, command.cwd)
  }

  private async launchService(
    name: 'api' | 'worker',
    command: string,
    args: string[],
    cwd: string,
  ): Promise<void> {
    const child = this.deps.spawn(command, args, {
      cwd,
      env: this.childEnv(),
      detached: process.platform !== 'win32',
      windowsHide: true,
    })
    this.attachOutput(name, child)
    const managed: ManagedChild = { name, child, startedAt: this.deps.now() }
    this.managed.set(name, managed)
    this.attachExit(name, child)
    try {
      await this.waitForSpawn(name, child)

      if (name === 'api') {
        await this.waitForHealth(managed)
      } else {
        await this.delay(this.config.workerGraceMs)
        if (this.stopping) throw new Error('启动已取消')
        if (child.exitCode !== null) {
          throw new Error(
            `后台 Worker 提前退出（退出码 ${child.exitCode}）；${this.childLogTail(name)}`,
          )
        }
        this.logger.info('后台 Worker 已启动')
      }
    } catch (error) {
      // 启动失败的进程也要回收，避免下一次重启覆盖记录后留下孤儿。
      if (this.managed.get(name)?.child === child) this.managed.delete(name)
      if (child.pid) await this.terminate(name, child)
      throw error
    }
  }

  private async waitForSpawn(name: string, child: ChildLike): Promise<void> {
    await new Promise<void>((resolve, reject) => {
      let settled = false
      const timer = setTimeout(() => {
        if (!settled) {
          settled = true
          resolve()
        }
      }, 3_000)
      timer.unref()
      child.once('spawn', () => {
        if (settled) return
        settled = true
        clearTimeout(timer)
        resolve()
      })
      child.once('error', (error: Error) => {
        if (settled) return
        settled = true
        clearTimeout(timer)
        reject(new Error(`${STAGE_LABELS[name === 'api' ? 'api' : 'worker']}无法启动：${error.message}`))
      })
    })
  }

  private async waitForHealth(managed: ManagedChild): Promise<void> {
    const url = `http://${this.config.host}:${this.port}/api/v1/health`
    const deadline = this.deps.now() + this.config.healthTimeoutMs
    while (this.deps.now() < deadline) {
      if (this.stopping) throw new Error('启动已取消')
      if (managed.child.exitCode !== null) {
        throw new Error(
          `API 服务提前退出（退出码 ${managed.child.exitCode}）；${this.childLogTail('api')}`,
        )
      }
      if (await this.deps.probeHttp(url, 1_500)) {
        this.logger.info(`API 健康检查通过：${url}`)
        return
      }
      await this.delay(this.config.healthPollIntervalMs)
    }
    throw new Error(
      `API 在 ${Math.round(this.config.healthTimeoutMs / 1000)} 秒内未通过健康检查；${this.childLogTail('api')}`,
    )
  }

  private attachOutput(name: string, child: ChildLike): void {
    child.stdout?.on('data', (chunk) => this.logger.child(name, chunk, 'stdout'))
    child.stderr?.on('data', (chunk) => this.logger.child(name, chunk, 'stderr'))
  }

  private childLogTail(name: string): string {
    const lines = this.logger.recentLines(12).filter((line) => line.includes(`[${name}]`))
    return lines.length > 0 ? `最近输出：${lines.slice(-4).join(' | ')}` : '没有可用的子进程输出'
  }

  private attachExit(name: 'api' | 'worker', child: ChildLike): void {
    child.on('exit', (code, signal) => {
      const current = this.managed.get(name)
      if (current?.child !== child) return
      this.managed.delete(name)
      const detail = code !== null ? `退出码 ${code}` : `信号 ${signal ?? '未知'}`
      if (this.stopping || this.failing || this.status.phase === 'stopping' || this.status.phase === 'failed') {
        this.logger.info(`${name} 已退出（${detail}）`)
        return
      }
      if (this.status.phase === 'starting') {
        this.logger.warn(`${name} 在启动阶段退出（${detail}）`)
        return
      }
      this.logger.warn(`${name} 异常退出（${detail}）`)
      void this.handleServiceExit(name, detail)
    })
    child.once('error', (error: Error) => {
      this.logger.error(`${name} 子进程错误：${error.message}`)
      if (this.status.phase === 'ready' && !this.stopping && !this.failing) {
        void this.handleServiceExit(name, error.message)
      }
    })
  }

  private async handleServiceExit(
    name: 'api' | 'worker',
    detail: string,
  ): Promise<void> {
    if (this.stopping || this.failing) return
    if (this.status.phase !== 'ready') return
    if (this.restarting.has(name)) return
    this.restarting.add(name)
    const label = STAGE_LABELS[name]
    let retryDetail: string | null = null
    let restarted = false
    try {
      const attempt = this.restartCounts[name] + 1
      this.restartCounts[name] = attempt
      if (attempt > this.config.maxRestarts) {
        await this.failStack(
          name,
          `${label}连续异常退出（${detail}），已重启 ${this.config.maxRestarts} 次仍未恢复`,
        )
        return
      }
      const delay = Math.min(
        this.config.restartBaseDelayMs * 2 ** (attempt - 1),
        this.config.restartMaxDelayMs,
      )
      this.logger.warn(
        `${label}异常退出（${detail}），${delay}ms 后第 ${attempt}/${this.config.maxRestarts} 次重启`,
      )
      this.setStatus({
        phase: 'ready',
        stage: 'ready',
        message: `${label}异常退出，正在第 ${attempt}/${this.config.maxRestarts} 次重启…`,
      })
      await this.delay(delay)
      if (this.stopping || this.status.phase !== 'ready') return
      try {
        if (name === 'api') {
          if (this.config.stub) {
            const stub = this.stubCommand('api', ['--port', String(this.port ?? 0)])
            await this.launchService('api', stub.command, stub.args, stub.cwd)
          } else {
            const command = this.sidecarCommand('serve', [
              '--host',
              this.config.host,
              '--port',
              String(this.port ?? 0),
            ])
            await this.launchService('api', command.command, command.args, command.cwd)
          }
        } else if (this.config.stub) {
          const stub = this.stubCommand('worker', [])
          await this.launchService('worker', stub.command, stub.args, stub.cwd)
        } else {
          const command = this.sidecarCommand('worker', [])
          await this.launchService('worker', command.command, command.args, command.cwd)
        }
        this.logger.info(`${label} 第 ${attempt} 次重启成功`)
        restarted = true
      } catch (error) {
        retryDetail = describeError(error)
        this.logger.error(`${label} 第 ${attempt} 次重启失败：${retryDetail}`)
      }
    } finally {
      // 先释放重启锁，再发布 ready，避免新进程立即退出时被误判为重复事件。
      this.restarting.delete(name)
    }
    if (retryDetail) {
      await this.handleServiceExit(name, `重启失败：${retryDetail}`)
    } else if (restarted) {
      this.setStatus({ phase: 'ready', stage: 'ready', message: READY_MESSAGE })
      this.scheduleRestartReset(name)
    }
  }

  private scheduleRestartReset(name: 'api' | 'worker'): void {
    const timer = setTimeout(() => {
      this.restartCounts[name] = 0
      this.resetTimers.delete(timer)
      this.logger.info(`${STAGE_LABELS[name]} 稳定运行，重启计数已清零`)
    }, 60_000)
    timer.unref()
    this.resetTimers.add(timer)
  }

  private async failStack(stage: StageName, message: string): Promise<void> {
    if (this.stopping || this.failing) return
    this.failing = true
    this.logger.error(`服务异常：${message}`)
    await this.stopChildren()
    this.failing = false
    const failure = this.buildFailure(stage, message)
    this.setStatus({
      phase: 'failed',
      stage,
      message: `${STAGE_LABELS[stage]}异常：${message}`,
      failure,
      port: this.port,
    })
  }

  private async stopChildren(): Promise<void> {
    this.stopPostgresMonitor()
    for (const timer of this.resetTimers) clearTimeout(timer)
    this.resetTimers.clear()
    await this.stopService('worker')
    await this.stopService('api')
    await this.stopPostgres()
  }

  private async stopService(name: 'api' | 'worker'): Promise<void> {
    const managed = this.managed.get(name)
    if (!managed) return
    this.managed.delete(name)
    this.logger.info(`正在停止 ${STAGE_LABELS[name]}（PID ${managed.child.pid ?? '未知'}）…`)
    await this.terminate(name, managed.child)
  }

  private async terminate(name: 'api' | 'worker', child: ChildLike): Promise<void> {
    if (child.exitCode !== null) return
    await new Promise<void>((resolve) => {
      let settled = false
      let timer: NodeJS.Timeout | undefined
      const done = () => {
        if (settled) return
        settled = true
        if (timer) clearTimeout(timer)
        resolve()
      }
      child.on('exit', done)
      timer = setTimeout(() => {
        this.logger.warn(`${STAGE_LABELS[name]} 未在超时内退出，强制结束`)
        void this.killChild(child, 'SIGKILL')
        const killTimer = setTimeout(done, 1_000)
        killTimer.unref()
      }, this.config.stopTimeoutMs)
      timer.unref()
      void this.killChild(child, 'SIGTERM')
    })
    this.logger.info(`${STAGE_LABELS[name]} 已停止`)
  }

  private async killChild(child: ChildLike, signal: NodeJS.Signals): Promise<void> {
    const pid = child.pid
    if (!pid) {
      try {
        child.kill(signal)
      } catch {
        // 进程可能已经退出。
      }
      return
    }
    if (process.platform === 'win32') {
      const args = ['/pid', String(pid), '/T']
      if (signal === 'SIGKILL') args.push('/F')
      await this.deps.run('taskkill', args, {
        cwd: this.config.dataDir,
        env: this.childEnv(),
        timeoutMs: 5_000,
      })
      return
    }
    try {
      // detached 启动后按进程组结束，确保 Node 子进程再派生的子进程一并退出。
      process.kill(-pid, signal)
      return
    } catch {
      // 没有独立进程组时退回单进程信号。
    }
    try {
      process.kill(pid, signal)
    } catch {
      try {
        child.kill(signal)
      } catch {
        // 已经退出。
      }
    }
  }

  private startPostgresMonitor(): void {
    const pid = this.readPostmasterPid()
    if (!pid) {
      this.logger.warn('未读取到 postmaster.pid，无法监控 PostgreSQL 进程')
      return
    }
    this.pgPid = pid
    this.pgMonitor = setInterval(() => {
      if (this.pgPid && !this.deps.isProcessAlive(this.pgPid)) {
        this.stopPostgresMonitor()
        void this.failStack('postgres', 'PostgreSQL 进程意外退出，整套服务已停止')
      }
    }, 2_000)
    this.pgMonitor.unref()
  }

  private stopPostgresMonitor(): void {
    if (this.pgMonitor) {
      clearInterval(this.pgMonitor)
      this.pgMonitor = null
    }
    this.pgPid = null
  }

  private readPostmasterPid(): number | null {
    try {
      const content = readFileSync(path.join(this.config.pgDataDir, 'postmaster.pid'), 'utf8')
      const pid = Number.parseInt(content.split(/\r?\n/)[0] ?? '', 10)
      return Number.isFinite(pid) && pid > 0 ? pid : null
    } catch {
      return null
    }
  }

  private async stopPostgres(): Promise<void> {
    if (!this.pgManaged) return
    this.pgManaged = false
    if (this.config.pgBin) {
      const result = await this.deps.run(
        toolPath(this.config.pgBin, 'pg_ctl'),
        [
          '-D',
          this.config.pgDataDir,
          '-m',
          'fast',
          '-w',
          '-t',
          String(Math.ceil(this.config.stopTimeoutMs / 1000)),
          'stop',
        ],
        {
          cwd: this.config.dataDir,
          env: this.childEnv(),
          timeoutMs: this.config.stopTimeoutMs + 5_000,
        },
      )
      if (result.code === 0) {
        this.logger.info('PostgreSQL 已正常停止')
        return
      }
      this.logger.warn(`pg_ctl stop 失败（退出码 ${result.code}），改为按 PID 结束`)
    }
    const pid = this.readPostmasterPid()
    if (!pid) return
    await this.killPid(pid, 'SIGTERM')
    await this.delay(1_500)
    if (this.deps.isProcessAlive(pid)) await this.killPid(pid, 'SIGKILL')
    this.logger.info('PostgreSQL 已停止')
  }

  private async killPid(pid: number, signal: NodeJS.Signals): Promise<void> {
    if (process.platform === 'win32') {
      const args = ['/pid', String(pid), '/T']
      if (signal === 'SIGKILL') args.push('/F')
      await this.deps.run('taskkill', args, {
        cwd: this.config.dataDir,
        env: this.childEnv(),
        timeoutMs: 5_000,
      })
      return
    }
    try {
      process.kill(-pid, signal)
    } catch {
      try {
        process.kill(pid, signal)
      } catch {
        // 已经退出。
      }
    }
  }

  private buildFailure(stage: StageName, message: string) {
    const tail = this.logger.recentLines(80)
    if (stage === 'postgres') tail.push(...this.readPostgresTail())
    return {
      stage,
      message,
      logTail: tail.slice(-80),
      logPath: this.logger.logPath,
    }
  }

  private readPostgresTail(): string[] {
    try {
      const content = readFileSync(path.join(this.config.logDir, 'postgres.log'), 'utf8')
      return content
        .split(/\r?\n/)
        .filter(Boolean)
        .slice(-30)
        .map((line) => `[postgres.log] ${brief(line, 400)}`)
    } catch {
      return []
    }
  }

  private delay(ms: number): Promise<void> {
    return ms > 0 ? this.deps.sleep(ms) : Promise.resolve()
  }
}
