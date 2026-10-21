/** 启动阶段严格按 PostgreSQL → migrate → API → Worker 推进。 */
export type StageName = 'postgres' | 'migrate' | 'api' | 'worker'

export type SupervisorPhase = 'idle' | 'starting' | 'ready' | 'failed' | 'stopping' | 'stopped'

export interface StartupFailure {
  stage: StageName
  /** 面向用户的中文说明；不包含连接串、口令或密钥。 */
  message: string
  /** 最近日志行，便于错误窗口直接展示。 */
  logTail: string[]
  logPath: string
}

export interface SupervisorStatus {
  phase: SupervisorPhase
  /** 已进入的阶段；ready 表示四个阶段全部完成。 */
  stage: StageName | 'ready' | 'idle'
  message: string
  port?: number
  failure?: StartupFailure
  updatedAt: string
}

export interface RunResult {
  code: number
  stdout: string
  stderr: string
  /** execFile 的错误码（例如 ENOENT），用于区分“命令不存在”和“命令失败”。 */
  errorCode?: string
}

export interface SpawnOptions {
  cwd: string
  env: NodeJS.ProcessEnv
  detached: boolean
  windowsHide: boolean
}

/** 仅暴露 supervisor 需要的方法，便于测试注入假子进程。 */
export interface ChildLike {
  pid?: number
  exitCode: number | null
  stdout: { on(event: 'data', listener: (chunk: Buffer | string) => void): unknown } | null
  stderr: { on(event: 'data', listener: (chunk: Buffer | string) => void): unknown } | null
  on(event: 'exit', listener: (code: number | null, signal: NodeJS.Signals | null) => void): unknown
  once(event: 'error', listener: (error: Error) => void): unknown
  once(event: 'spawn', listener: () => void): unknown
  kill(signal?: NodeJS.Signals): boolean
}

export type SpawnFn = (command: string, args: string[], options: SpawnOptions) => ChildLike
export type RunFn = (command: string, args: string[], options: { cwd: string; env: NodeJS.ProcessEnv; timeoutMs: number }) => Promise<RunResult>
export type ProbeHttpFn = (url: string, timeoutMs: number) => Promise<boolean>
export type FindFreePortFn = () => Promise<number>

export interface SupervisorDeps {
  spawn: SpawnFn
  run: RunFn
  probeHttp: ProbeHttpFn
  findFreePort: FindFreePortFn
  sleep: (ms: number) => Promise<void>
  now: () => number
  isProcessAlive: (pid: number) => boolean
}

export interface RuntimeConfig {
  /** 桌面数据根目录（绝对路径）。 */
  userDataDir: string
  dataDir: string
  storageDir: string
  backupDir: string
  logDir: string
  runDir: string
  pgDataDir: string
  /** sidecar 在开发模式下的工作目录（仓库 backend/）。 */
  backendDir: string
  /** 供 sidecar 同源托管的前端构建目录；桌面模式下由 P004 接管。 */
  staticDir: string | null
  host: '127.0.0.1'
  /** 0 表示由监管器选择一个空闲 loopback 端口。 */
  apiPort: number
  healthTimeoutMs: number
  healthPollIntervalMs: number
  workerGraceMs: number
  pgReadyTimeoutMs: number
  pgPollIntervalMs: number
  stopTimeoutMs: number
  restartBaseDelayMs: number
  restartMaxDelayMs: number
  maxRestarts: number
  pgMode: 'managed' | 'external' | 'skip'
  pgBin: string | null
  pgPort: number
  pgUser: string
  pgDatabase: string
  sidecarBin: string | null
  sidecarModule: string
  pythonCommand: string
  stub: boolean
  stubDelayMs: number
  stubFailStage: StageName | null
  stubFailAlways: boolean
  externalAllowlist: string[]
  devTools: boolean
}
