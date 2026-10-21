import { execFile, spawn } from 'node:child_process'
import http from 'node:http'
import net from 'node:net'

import type { ChildLike, RunResult, SpawnOptions, SupervisorDeps } from './types'

function runCommand(
  command: string,
  args: string[],
  options: { cwd: string; env: NodeJS.ProcessEnv; timeoutMs: number },
): Promise<RunResult> {
  return new Promise((resolve) => {
    execFile(
      command,
      args,
      {
        cwd: options.cwd,
        env: options.env,
        timeout: options.timeoutMs,
        maxBuffer: 1024 * 1024,
        windowsHide: true,
        encoding: 'utf8',
      },
      (error, stdout, stderr) => {
        if (!error) {
          resolve({ code: 0, stdout: String(stdout ?? ''), stderr: String(stderr ?? '') })
          return
        }
        const err = error as NodeJS.ErrnoException & { code?: string | number; killed?: boolean }
        const errorCode = typeof err.code === 'string' ? err.code : undefined
        const code =
          typeof err.code === 'number' ? err.code : err.killed ? 124 : errorCode ? 127 : 1
        resolve({
          code,
          stdout: String(stdout ?? ''),
          stderr: String(stderr ?? ''),
          ...(errorCode ? { errorCode } : {}),
        })
      },
    )
  })
}

function probeHttp(url: string, timeoutMs: number): Promise<boolean> {
  return new Promise((resolve) => {
    let settled = false
    const finish = (ok: boolean) => {
      if (settled) return
      settled = true
      resolve(ok)
    }
    const request = http.get(url, { timeout: timeoutMs }, (response) => {
      response.resume()
      finish(response.statusCode === 200)
    })
    request.once('timeout', () => {
      request.destroy()
      finish(false)
    })
    request.once('error', () => finish(false))
  })
}

function findFreePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const server = net.createServer()
    server.unref()
    server.once('error', reject)
    server.listen(0, '127.0.0.1', () => {
      const address = server.address()
      const port = typeof address === 'object' && address ? address.port : 0
      server.close(() => (port > 0 ? resolve(port) : reject(new Error('没有可用的本机端口'))))
    })
  })
}

/** 真实运行时依赖；单元测试可整体替换为假子进程模块。 */
export const defaultDeps: SupervisorDeps = {
  spawn: (command: string, args: string[], options: SpawnOptions) =>
    spawn(command, args, {
      cwd: options.cwd,
      env: options.env,
      detached: options.detached,
      windowsHide: options.windowsHide,
      stdio: ['ignore', 'pipe', 'pipe'],
    }) as unknown as ChildLike,
  run: runCommand,
  probeHttp,
  findFreePort,
  sleep: (ms: number) => new Promise((resolve) => setTimeout(resolve, ms)),
  now: () => Date.now(),
  isProcessAlive: (pid: number) => {
    try {
      process.kill(pid, 0)
      return true
    } catch {
      return false
    }
  },
}
