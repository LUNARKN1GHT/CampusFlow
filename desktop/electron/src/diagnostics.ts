import { app } from 'electron'
import { mkdirSync, writeFileSync } from 'node:fs'
import path from 'node:path'

import { summarizePaths } from './config'
import type { Logger } from './logger'
import type { RuntimeConfig, SupervisorStatus } from './types'

export interface DiagnosticsInput {
  config: RuntimeConfig
  status: SupervisorStatus
  logger: Logger
}

/**
 * 诊断报告只包含版本、路径、端口、阶段与已脱敏的最近日志；
 * 不读取环境变量，不包含数据库口令、模型密钥或 OAuth 凭据。
 */
export function buildDiagnostics(input: DiagnosticsInput): Record<string, unknown> {
  return {
    generatedAt: new Date().toISOString(),
    app: {
      name: app.getName(),
      version: app.getVersion(),
      electron: process.versions.electron,
      chrome: process.versions.chrome,
      node: process.versions.node,
      platform: process.platform,
      arch: process.arch,
    },
    runtime: {
      ...summarizePaths(input.config),
      host: input.config.host,
      apiPort: input.status.port ?? input.config.apiPort,
      apiPortResolved: input.status.port ?? null,
      pgMode: input.config.pgMode,
      pgPort: input.config.pgPort,
      pgBin: input.config.pgBin,
      sidecarBin: input.config.sidecarBin,
      sidecarModule: input.config.sidecarBin ? null : input.config.sidecarModule,
      staticDir: input.config.staticDir,
      stub: input.config.stub,
      externalAllowlist: input.config.externalAllowlist,
    },
    supervisor: {
      phase: input.status.phase,
      stage: input.status.stage,
      message: input.status.message,
      failure: input.status.failure ?? null,
    },
    recentLog: input.logger.recentLines(200),
  }
}

export function writeDiagnostics(input: DiagnosticsInput): string {
  mkdirSync(input.config.logDir, { recursive: true })
  const stamp = new Date().toISOString().replace(/[:.]/g, '-')
  const target = path.join(input.config.logDir, `diagnostics-${stamp}.json`)
  writeFileSync(target, `${JSON.stringify(buildDiagnostics(input), null, 2)}\n`, {
    encoding: 'utf8',
    mode: 0o600,
  })
  return target
}
