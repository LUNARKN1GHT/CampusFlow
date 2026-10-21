import { appendFileSync, mkdirSync } from 'node:fs'
import path from 'node:path'

type Level = 'INFO' | 'WARN' | 'ERROR'

const SECRET_PATTERN =
  /((?:api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|token|password|passwd|secret|authorization)\s*[:=]\s*)([^\s,;]+)/gi
const URL_CREDENTIAL_PATTERN = /(https?:\/\/)[^/\s:@]+:[^/\s@]+@/gi
const BEARER_TOKEN_PATTERN = /\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+/gi

/** 日志与错误页共用同一套脱敏规则：密钥、令牌与连接串口令不落盘。 */
export function redact(text: string): string {
  return text
    .replace(BEARER_TOKEN_PATTERN, '$1 [已隐藏]')
    .replace(SECRET_PATTERN, '$1[已隐藏]')
    .replace(URL_CREDENTIAL_PATTERN, '$1[已隐藏]@')
}

export interface LoggerOptions {
  maxRecentLines?: number
  fileName?: string
}

/**
 * 极简文件日志：主进程写 main.log，子进程分阶段写 stage-<name>.log。
 * 所有写入失败都降级为静默，日志问题不能阻断启动或退出。
 */
export class Logger {
  readonly logPath: string
  readonly logDir: string
  private readonly recent: string[] = []
  private readonly maxRecentLines: number

  constructor(logDir: string, options: LoggerOptions = {}) {
    this.logDir = logDir
    this.maxRecentLines = options.maxRecentLines ?? 300
    this.logPath = path.join(logDir, options.fileName ?? 'main.log')
    try {
      mkdirSync(logDir, { recursive: true })
    } catch {
      // 目录不可写时只保留内存日志。
    }
  }

  info(message: string): void {
    this.write('INFO', 'main', message)
  }

  warn(message: string): void {
    this.write('WARN', 'main', message)
  }

  error(message: string): void {
    this.write('ERROR', 'main', message)
  }

  /** 子进程输出：按行写入独立阶段文件，并进入错误页使用的最近日志缓冲。 */
  child(name: string, chunk: Buffer | string, stream: 'stdout' | 'stderr' = 'stdout'): void {
    const text = chunk.toString()
    for (const rawLine of text.split(/\r?\n/)) {
      const line = rawLine.trimEnd()
      if (!line) continue
      const level: Level = stream === 'stderr' ? 'WARN' : 'INFO'
      this.write(level, name, line)
    }
  }

  recentLines(limit = 80): string[] {
    return this.recent.slice(-limit)
  }

  private write(level: Level, scope: string, message: string): void {
    const timestamp = new Date().toISOString()
    const line = `${timestamp} [${level}] [${scope}] ${redact(message)}`
    this.recent.push(line)
    if (this.recent.length > this.maxRecentLines) {
      this.recent.splice(0, this.recent.length - this.maxRecentLines)
    }
    const file = scope === 'main' ? this.logPath : path.join(this.logDir, `stage-${scope}.log`)
    try {
      appendFileSync(file, `${line}\n`, { encoding: 'utf8', mode: 0o600 })
    } catch {
      // 日志写入失败不抛错；进程监管不能依赖磁盘状态。
    }
  }
}
