import { existsSync } from 'node:fs'
import path from 'node:path'

import type { RuntimeConfig, StageName } from './types'

const STAGES: readonly StageName[] = ['postgres', 'migrate', 'api', 'worker']

function positiveInt(value: string | undefined, fallback: number, min = 1): number {
  if (value === undefined || value.trim() === '') return fallback
  const parsed = Number.parseInt(value, 10)
  return Number.isFinite(parsed) && parsed >= min ? parsed : fallback
}

function absoluteFrom(base: string, value: string | undefined, fallback: string): string {
  if (!value) return fallback
  return path.isAbsolute(value) ? path.normalize(value) : path.resolve(base, value)
}

function parseAllowlist(value: string | undefined): string[] {
  const entries = (value ?? 'https://accounts.google.com')
    .split(',')
    .map((entry) => entry.trim())
    .filter(Boolean)
  const origins: string[] = []
  for (const entry of entries) {
    try {
      const url = new URL(entry)
      // 只接受精确 HTTPS 来源，不接受通配符、凭据或 http。
      if (url.protocol !== 'https:' || url.username || url.password) continue
      origins.push(url.origin)
    } catch {
      // 非法条目直接忽略，外链只会更严格。
    }
  }
  return [...new Set(origins)]
}

function parseStage(value: string | undefined): StageName | null {
  if (!value) return null
  return STAGES.includes(value as StageName) ? (value as StageName) : null
}


/** 打包后 electron-builder 把 sidecar／pgsql／static 放在 Resources 下；源码运行没有该目录。 */
function bundledPath(...parts: string[]): string | null {
  const resources = (process as NodeJS.Process & { resourcesPath?: string }).resourcesPath
  if (!resources) return null
  const candidate = path.join(resources, ...parts)
  return existsSync(candidate) ? candidate : null
}

function resolveStaticDir(env: NodeJS.ProcessEnv): string | null {
  if (env.CAMPUSFLOW_STATIC_DIR) return path.resolve(env.CAMPUSFLOW_STATIC_DIR)
  const bundled = bundledPath('static')
  if (bundled) return bundled
  const repositoryBuild = path.resolve(__dirname, '..', '..', '..', 'frontend', 'dist')
  return existsSync(repositoryBuild) ? repositoryBuild : null
}

/**
 * 从环境变量解析桌面运行配置。所有路径都转换为绝对路径；
 * 端口、超时与重试次数均可覆盖，便于本机联调与自动化测试。
 */
export function loadConfig(env: NodeJS.ProcessEnv, userDataDir: string): RuntimeConfig {
  const base = path.resolve(userDataDir)
  const dataDir = absoluteFrom(base, env.CAMPUSFLOW_DATA_DIR, path.join(base, 'data'))
  const pgBin = env.CAMPUSFLOW_PG_BIN
    ? path.resolve(env.CAMPUSFLOW_PG_BIN)
    : bundledPath('pgsql', 'bin')
  const requestedMode = env.CAMPUSFLOW_PG_MODE
  const pgMode =
    requestedMode === 'managed' || requestedMode === 'external' || requestedMode === 'skip'
      ? requestedMode
      : pgBin
        ? 'managed'
        : 'external'
  const pgPort = positiveInt(env.CAMPUSFLOW_PG_PORT, pgMode === 'managed' ? 55432 : 5432)
  const apiPort = positiveInt(env.CAMPUSFLOW_API_PORT, 0, 1)

  return {
    userDataDir: base,
    dataDir,
    storageDir: absoluteFrom(base, env.CAMPUSFLOW_STORAGE_DIR, path.join(dataDir, 'materials')),
    backupDir: absoluteFrom(base, env.CAMPUSFLOW_BACKUP_DIR, path.join(base, 'backups')),
    logDir: absoluteFrom(base, env.CAMPUSFLOW_LOG_DIR, path.join(base, 'logs')),
    runDir: absoluteFrom(base, env.CAMPUSFLOW_RUN_DIR, path.join(base, 'run')),
    pgDataDir: absoluteFrom(base, env.CAMPUSFLOW_PG_DATA_DIR, path.join(dataDir, 'pgdata')),
    backendDir: absoluteFrom(
      path.resolve(__dirname, '..', '..', '..'),
      env.CAMPUSFLOW_BACKEND_DIR,
      path.resolve(__dirname, '..', '..', '..', 'backend'),
    ),
    staticDir: resolveStaticDir(env),
    host: '127.0.0.1',
    apiPort,
    healthTimeoutMs: positiveInt(env.CAMPUSFLOW_HEALTH_TIMEOUT_MS, 60_000),
    healthPollIntervalMs: positiveInt(env.CAMPUSFLOW_HEALTH_POLL_MS, 300),
    workerGraceMs: positiveInt(env.CAMPUSFLOW_WORKER_GRACE_MS, 800),
    pgReadyTimeoutMs: positiveInt(env.CAMPUSFLOW_PG_READY_TIMEOUT_MS, 30_000),
    pgPollIntervalMs: positiveInt(env.CAMPUSFLOW_PG_POLL_MS, 500),
    stopTimeoutMs: positiveInt(env.CAMPUSFLOW_STOP_TIMEOUT_MS, 5_000),
    restartBaseDelayMs: positiveInt(env.CAMPUSFLOW_RESTART_BASE_MS, 500),
    restartMaxDelayMs: positiveInt(env.CAMPUSFLOW_RESTART_MAX_MS, 4_000),
    maxRestarts: positiveInt(env.CAMPUSFLOW_MAX_RESTARTS, 3, 0),
    pgMode,
    pgBin,
    pgPort,
    pgUser: env.CAMPUSFLOW_PG_USER?.trim() || 'campusflow',
    pgDatabase: env.CAMPUSFLOW_PG_DATABASE?.trim() || 'campusflow',
    sidecarBin: env.CAMPUSFLOW_SIDECAR_BIN
      ? path.resolve(env.CAMPUSFLOW_SIDECAR_BIN)
      : bundledPath(
          'sidecar',
          process.platform === 'win32' ? 'campusflow-sidecar.exe' : 'campusflow-sidecar',
        ),
    sidecarModule: env.CAMPUSFLOW_SIDECAR_MODULE?.trim() || 'campusflow.sidecar',
    pythonCommand: env.CAMPUSFLOW_PYTHON?.trim() || 'python',
    stub: env.CAMPUSFLOW_ELECTRON_STUB === '1',
    stubDelayMs: positiveInt(env.CAMPUSFLOW_ELECTRON_STUB_DELAY_MS, 120, 0),
    stubFailStage: parseStage(env.CAMPUSFLOW_ELECTRON_STUB_FAIL_STAGE),
    stubFailAlways: env.CAMPUSFLOW_ELECTRON_STUB_FAIL_ALWAYS === '1',
    externalAllowlist: parseAllowlist(env.CAMPUSFLOW_EXTERNAL_ALLOWLIST),
    devTools: env.CAMPUSFLOW_ELECTRON_DEVTOOLS === '1',
  }
}

/** 供日志与诊断使用的路径摘要，不含密钥。 */
export function summarizePaths(config: RuntimeConfig): Record<string, string> {
  return {
    userDataDir: config.userDataDir,
    dataDir: config.dataDir,
    storageDir: config.storageDir,
    backupDir: config.backupDir,
    logDir: config.logDir,
    runDir: config.runDir,
    pgDataDir: config.pgDataDir,
    backendDir: config.backendDir,
    staticDir: config.staticDir ?? '(未设置)',
  }
}
