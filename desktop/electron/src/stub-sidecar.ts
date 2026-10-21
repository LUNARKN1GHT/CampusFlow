/**
 * 仅用于 CAMPUSFLOW_ELECTRON_STUB=1 的本地测试桩：
 * 以独立子进程模拟 API 或 Worker，写入 PID 文件并支持 SIGTERM 优雅退出，
 * 让 Electron 冒烟测试不依赖 Python 与 PostgreSQL。
 *
 * 这不是产品运行路径；真实构建由 P002a sidecar 与 P003a PostgreSQL 提供。
 */
import fs from 'node:fs'
import http from 'node:http'
import path from 'node:path'

interface Options {
  role: 'api' | 'worker'
  port: number
  pidFile: string
  runDir: string
}

function parseArgs(argv: string[]): Options {
  let role: 'api' | 'worker' = 'api'
  let port = 0
  let pidFile = ''
  let runDir = ''
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index]
    const next = argv[index + 1]
    if (arg === '--role' && (next === 'api' || next === 'worker')) {
      role = next
      index += 1
    } else if (arg === '--port' && next) {
      port = Number.parseInt(next, 10) || 0
      index += 1
    } else if (arg === '--pid-file' && next) {
      pidFile = next
      index += 1
    } else if (arg === '--run-dir' && next) {
      runDir = next
      index += 1
    }
  }
  if (!pidFile) {
    pidFile = path.join(runDir || process.cwd(), `stub-${role}.pid`)
  }
  return { role, port, pidFile, runDir: runDir || process.cwd() }
}

const options = parseArgs(process.argv.slice(2))
const log = (message: string) => {
  process.stdout.write(`[stub-${options.role}] ${message}\n`)
}

fs.mkdirSync(path.dirname(options.pidFile), { recursive: true })
fs.writeFileSync(options.pidFile, `${process.pid}\n`, { encoding: 'utf8' })
log(`pid ${process.pid} -> ${options.pidFile}`)

const PAGE = `<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>CampusFlow</title>
    <style>
      body { font-family: system-ui, -apple-system, "Segoe UI", sans-serif; margin: 0; padding: 48px; background: #f6f7f9; color: #1f2933; }
      main { max-width: 640px; margin: 0 auto; }
      h1 { font-size: 24px; }
      code { background: #eef1f5; padding: 2px 6px; border-radius: 4px; }
    </style>
  </head>
  <body>
    <main>
      <h1 data-testid="app-ready">CampusFlow 桌面版已就绪</h1>
      <p>这是 Electron 冒烟测试使用的本地测试桩页面，前端构建产物由 API 同源托管。</p>
      <p>API: <code>http://127.0.0.1:${options.port}/api/v1/health</code></p>
    </main>
  </body>
</html>`

function json(response: http.ServerResponse, status: number, body: unknown): void {
  const payload = JSON.stringify(body)
  response.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Content-Length': Buffer.byteLength(payload),
    'Cache-Control': 'no-store',
  })
  response.end(payload)
}

const server = http.createServer((request, response) => {
  const url = new URL(request.url ?? '/', 'http://127.0.0.1')
  if (url.pathname === '/api/v1/health') {
    json(response, 200, { status: 'ok', service: 'campusflow-api' })
    return
  }
  if (url.pathname === '/api/v1/ready') {
    json(response, 200, { status: 'ok', service: 'campusflow-api', database: 'ok' })
    return
  }
  if (url.pathname.startsWith('/api/')) {
    json(response, 404, { detail: '测试桩没有此接口' })
    return
  }
  response.writeHead(200, {
    'Content-Type': 'text/html; charset=utf-8',
    'Cache-Control': 'no-store',
  })
  response.end(PAGE)
})

let shuttingDown = false
function shutdown(signal: string): void {
  if (shuttingDown) return
  shuttingDown = true
  log(`${signal} received, shutting down gracefully`)
  try {
    fs.rmSync(options.pidFile, { force: true })
  } catch {
    // PID 文件可能已被清理。
  }
  if (options.role === 'api') {
    server.close(() => {
      log('http server closed')
      process.exit(0)
    })
    setTimeout(() => process.exit(0), 2_000).unref()
  } else {
    process.exit(0)
  }
}

process.on('SIGTERM', () => shutdown('SIGTERM'))
process.on('SIGINT', () => shutdown('SIGINT'))

if (options.role === 'api') {
  server.listen(options.port, '127.0.0.1', () => {
    const address = server.address()
    const actual = typeof address === 'object' && address ? address.port : options.port
    log(`listening on http://127.0.0.1:${actual}`)
  })
  server.on('error', (error) => {
    process.stderr.write(`[stub-api] server error: ${error.message}\n`)
    process.exit(1)
  })
} else {
  log('worker started (no-op poll)')
  // 不 unref：Worker 必须像真实后台进程一样持续存活，直到收到 SIGTERM。
  setInterval(() => log('worker heartbeat'), 5_000)
}
