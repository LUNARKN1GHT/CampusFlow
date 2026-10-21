import { app, BrowserWindow, dialog, ipcMain, Menu, session, shell } from 'electron'
import path from 'node:path'

import { loadConfig } from './config'
import { attemptAutoLogin, autoLoginNoticeMessage, buildAutoLoginNoticeScript } from './auto-login'
import { writeDiagnostics } from './diagnostics'
import { Logger } from './logger'
import { installSecurity, isAllowedExternalUrl } from './security'
import { Supervisor } from './supervisor'
import type { RuntimeConfig, SupervisorStatus } from './types'

// 允许测试与排障把用户目录指向临时位置；必须在 ready 与单实例锁之前设置。
const userDataOverride = process.env.CAMPUSFLOW_ELECTRON_USER_DATA
if (userDataOverride) app.setPath('userData', path.resolve(userDataOverride))

app.setName('CampusFlow')

const rendererDir = path.join(__dirname, 'renderer')
let config: RuntimeConfig | null = null
let logger: Logger | null = null
let supervisor: Supervisor | null = null
let shellWindow: BrowserWindow | null = null
let mainWindow: BrowserWindow | null = null
let shellView: 'splash' | 'error' = 'splash'
let currentStatus: SupervisorStatus | null = null
let ipcInstalled = false
let quitting = false
let shutdownPromise: Promise<void> | null = null
let autoLoginSettled = false
let autoLoginRunning = false

function getAppOrigin(): string | null {
  const port = supervisor?.getPort() ?? config?.apiPort ?? 0
  return port > 0 ? `http://127.0.0.1:${port}` : null
}

function secureWebPreferences(): Electron.WebPreferences {
  return {
    preload: path.join(__dirname, 'preload.js'),
    contextIsolation: true,
    nodeIntegration: false,
    sandbox: true,
    webSecurity: true,
    allowRunningInsecureContent: false,
    webviewTag: false,
    spellcheck: false,
    devTools: config?.devTools ?? false,
  }
}

// 前端 OAuth 使用 window.open(...) 轮询连接状态；桌面外壳改为系统浏览器，
// 并返回一个最小的窗口占位对象，避免页面把“已交给系统浏览器”误判为弹窗被拦截。
// 实际是否打开仍由主进程按 HTTPS 来源白名单校验。
const OAUTH_WINDOW_OPEN_PATCH = `(() => {
  if (window.__campusflowWindowOpenPatched) return;
  window.__campusflowWindowOpenPatched = true;
  const nativeOpen = window.open.bind(window);
  window.open = function (url, name, features) {
    if (
      name === 'campusflow-calendar-oauth' &&
      typeof url === 'string' &&
      /^https:\\/\\//i.test(url) &&
      window.campusflow &&
      typeof window.campusflow.openExternal === 'function'
    ) {
      void window.campusflow.openExternal(url);
      return { closed: false, close() {}, focus() {}, blur() {}, location: { href: url } };
    }
    return nativeOpen(url, name, features);
  };
})();`

function broadcastStatus(): void {
  if (!currentStatus) return
  for (const window of [shellWindow, mainWindow]) {
    if (window && !window.isDestroyed()) {
      window.webContents.send('campusflow:status', currentStatus)
    }
  }
}

function createShellWindow(): BrowserWindow {
  if (shellWindow && !shellWindow.isDestroyed()) return shellWindow
  shellWindow = new BrowserWindow({
    width: 460,
    height: 320,
    show: true,
    resizable: false,
    maximizable: false,
    fullscreenable: false,
    title: 'CampusFlow',
    backgroundColor: '#f6f7f9',
    webPreferences: secureWebPreferences(),
  })
  shellWindow.on('closed', () => {
    shellWindow = null
  })
  shellView = 'splash'
  void shellWindow.loadFile(path.join(rendererDir, 'splash.html')).catch((error: unknown) => {
    const message = error instanceof Error ? error.message : String(error)
    logger?.error(`启动页加载失败：${message}`)
    // 连本地启动页都无法加载时仍给出可见错误，而不是留下空白窗口。
    dialog.showErrorBox(
      'CampusFlow 启动失败',
      `无法加载本地启动页：${message}\n日志：${logger?.logPath ?? '未知'}`,
    )
    void shutdown().finally(() => app.exit(1))
  })
  return shellWindow
}

async function showSplash(): Promise<void> {
  const target = createShellWindow()
  if (shellView !== 'splash' || target.webContents.isLoading()) {
    shellView = 'splash'
    target.setSize(460, 320)
    target.setResizable(false)
    try {
      await target.loadFile(path.join(rendererDir, 'splash.html'))
    } catch (error) {
      logger?.error(`启动页重新加载失败：${error instanceof Error ? error.message : String(error)}`)
    }
  }
  broadcastStatus()
}

async function showError(status: SupervisorStatus): Promise<void> {
  if (!status.failure) return
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.destroy()
    mainWindow = null
  }
  const target = createShellWindow()
  if (shellView !== 'error') {
    shellView = 'error'
    target.setSize(680, 640)
    target.setResizable(true)
    try {
      await target.loadFile(path.join(rendererDir, 'error.html'))
    } catch (error) {
      logger?.error(`错误页加载失败：${error instanceof Error ? error.message : String(error)}`)
    }
  }
  broadcastStatus()
}

/** 自动登录失败提示：只给用户名，不含口令；页面仍可手动登录。 */
function showAutoLoginNotice(username: string): void {
  const message = autoLoginNoticeMessage(username)
  logger?.warn(message)
  if (currentStatus && shellWindow && !shellWindow.isDestroyed()) {
    shellWindow.webContents.send('campusflow:status', { ...currentStatus, message })
  }
  const target = mainWindow
  if (!target || target.isDestroyed()) return
  void target.webContents
    .executeJavaScript(buildAutoLoginNoticeScript(username))
    .catch((error: unknown) => {
      logger?.warn(`自动登录提示注入失败：${error instanceof Error ? error.message : String(error)}`)
    })
}

/**
 * 主窗口加载完成或前端路由切到 /login 时尝试自动登录。
 * 凭据只来自 supervisor 的内存，不暴露给 preload／IPC，也不写入 localStorage。
 */
function runAutoLogin(window: BrowserWindow): void {
  if (!config || config.stub || !config.autoLogin) return
  const credentials = supervisor?.getBootstrapLogin()
  if (!credentials) return
  if (autoLoginSettled || autoLoginRunning) return
  autoLoginRunning = true
  void attemptAutoLogin(window.webContents, credentials, { logger: logger ?? undefined })
    .then((outcome) => {
      if (outcome === 'success') {
        autoLoginSettled = true
        return
      }
      if (outcome === 'failed') {
        autoLoginSettled = true
        showAutoLoginNotice(credentials.username)
      }
    })
    .catch((error: unknown) => {
      autoLoginSettled = true
      logger?.error(`自动登录异常：${error instanceof Error ? error.message : String(error)}`)
      showAutoLoginNotice(credentials.username)
    })
    .finally(() => {
      autoLoginRunning = false
    })
}

async function openMainWindow(): Promise<void> {
  if (mainWindow && !mainWindow.isDestroyed()) return
  const origin = getAppOrigin()
  if (!origin) {
    logger?.error('无法打开主窗口：API 端口尚未确定')
    return
  }
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 860,
    minWidth: 960,
    minHeight: 640,
    show: false,
    title: 'CampusFlow',
    backgroundColor: '#f6f7f9',
    webPreferences: secureWebPreferences(),
  })
  const created = mainWindow
  let loadFailed = false
  created.webContents.on('dom-ready', () => {
    void created.webContents.executeJavaScript(OAUTH_WINDOW_OPEN_PATCH).catch((error: unknown) => {
      logger?.error(`OAuth 外链补丁注入失败：${error instanceof Error ? error.message : String(error)}`)
    })
  })
  created.webContents.on('page-title-updated', (event) => {
    event.preventDefault()
    created.setTitle('CampusFlow')
  })
  // 首次加载完成后尝试自动登录；前端路由异步切到 /login 时再触发一次。
  created.webContents.on('did-finish-load', () => runAutoLogin(created))
  created.webContents.on('did-navigate-in-page', () => runAutoLogin(created))
  created.once('ready-to-show', () => {
    created.show()
    if (shellWindow && !shellWindow.isDestroyed()) {
      shellWindow.destroy()
      shellWindow = null
    }
  })
  created.on('closed', () => {
    if (mainWindow === created) mainWindow = null
  })
  created.webContents.on('did-fail-load', (_event, code, description, url, isMainFrame) => {
    if (!isMainFrame || loadFailed) return
    loadFailed = true
    logger?.error(`主窗口加载失败：${code} ${description} ${url}`)
    const failureStatus: SupervisorStatus = {
      phase: 'failed',
      stage: 'api',
      message: `界面加载失败：${description}`,
      port: supervisor?.getPort(),
      updatedAt: new Date().toISOString(),
      failure: {
        stage: 'api',
        message: `界面加载失败（${code} ${description}）`,
        logTail: logger?.recentLines(80) ?? [],
        logPath: logger?.logPath ?? '',
      },
    }
    currentStatus = failureStatus
    void showError(failureStatus)
  })
  try {
    // 只加载 loopback 上的同源 SPA；history 路由由服务端回退到 index.html。
    await created.loadURL(`${origin}/`)
  } catch (error) {
    if (!loadFailed) {
      loadFailed = true
      const message = error instanceof Error ? error.message : String(error)
      logger?.error(`主窗口加载异常：${message}`)
      const failureStatus: SupervisorStatus = {
        phase: 'failed',
        stage: 'api',
        message: `界面加载失败：${message}`,
        port: supervisor?.getPort(),
        updatedAt: new Date().toISOString(),
        failure: {
          stage: 'api',
          message: `界面加载失败：${message}`,
          logTail: logger?.recentLines(80) ?? [],
          logPath: logger?.logPath ?? '',
        },
      }
      currentStatus = failureStatus
      await showError(failureStatus)
    }
  }
}

function handleStatus(status: SupervisorStatus): void {
  currentStatus = status
  if (status.phase === 'failed' && status.failure) {
    void showError(status)
    return
  }
  if (status.phase === 'ready' && status.stage === 'ready' && !mainWindow) {
    void openMainWindow()
    broadcastStatus()
    return
  }
  if (status.phase === 'starting' && (shellView === 'error' || !shellWindow)) {
    void showSplash()
    return
  }
  broadcastStatus()
}

async function requestRetry(): Promise<void> {
  if (!supervisor) return
  const phase = supervisor.getStatus().phase
  if (phase === 'starting') return
  if (phase === 'ready') {
    // 服务仍在运行、只是主窗口加载失败时，重试只需重新加载界面，不重启整套服务。
    currentStatus = supervisor.getStatus()
    await openMainWindow()
    return
  }
  await showSplash()
  void supervisor.retry().catch((error: unknown) => {
    logger?.error(`重试启动失败：${error instanceof Error ? error.message : String(error)}`)
  })
}

function installIpc(): void {
  if (ipcInstalled) return
  ipcInstalled = true
  ipcMain.handle('campusflow:get-status', () => currentStatus ?? supervisor?.getStatus() ?? null)
  ipcMain.handle('campusflow:retry', async () => {
    await requestRetry()
  })
  ipcMain.handle('campusflow:open-logs', async () => {
    if (!config) return ''
    await shell.openPath(config.logDir)
    return config.logDir
  })
  ipcMain.handle('campusflow:open-external', async (_event, url: unknown) => {
    if (!config || typeof url !== 'string' || !isAllowedExternalUrl(url, config.externalAllowlist)) {
      logger?.warn(`拒绝打开外部链接：${typeof url === 'string' ? url : '非字符串'}`)
      return false
    }
    await shell.openExternal(url)
    logger?.info(`已在系统浏览器打开授权链接：${new URL(url).origin}`)
    return true
  })
}

function reloadActive(): void {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.reload()
    return
  }
  if (currentStatus?.phase === 'failed') {
    void requestRetry()
    return
  }
  if (shellWindow && !shellWindow.isDestroyed()) shellWindow.reload()
}

async function saveDiagnostics(): Promise<void> {
  if (!config || !logger) return
  try {
    const target = writeDiagnostics({
      config,
      status: currentStatus ?? supervisor?.getStatus() ?? {
        phase: 'idle',
        stage: 'idle',
        message: '尚未启动',
        updatedAt: new Date().toISOString(),
      },
      logger,
    })
    logger.info(`诊断报告已保存：${target}`)
    await dialog.showMessageBox({ type: 'info', message: '诊断信息已保存', detail: target })
  } catch (error) {
    logger.error(`诊断报告保存失败：${error instanceof Error ? error.message : String(error)}`)
  }
}

function buildMenu(): void {
  const template: Electron.MenuItemConstructorOptions[] = [
    {
      label: 'CampusFlow',
      submenu: [
        { label: '重新加载', click: () => reloadActive() },
        { label: '打开日志目录', click: () => void shell.openPath(config?.logDir ?? '') },
        { label: '保存诊断信息', click: () => void saveDiagnostics() },
        { type: 'separator' },
        { label: '退出', role: 'quit' },
      ],
    },
    {
      label: '编辑',
      submenu: [
        { label: '复制', role: 'copy' },
        { label: '全选', role: 'selectAll' },
      ],
    },
  ]
  if (config?.devTools) {
    template.push({
      label: '视图',
      submenu: [{ label: '开发者工具', role: 'toggleDevTools' }],
    })
  }
  Menu.setApplicationMenu(Menu.buildFromTemplate(template))
}

function focusExistingWindow(): void {
  const target = mainWindow ?? shellWindow
  if (!target || target.isDestroyed()) return
  if (target.isMinimized()) target.restore()
  target.show()
  target.focus()
}

async function shutdown(): Promise<void> {
  if (shutdownPromise) return shutdownPromise
  shutdownPromise = (async () => {
    logger?.info('收到退出请求：按 Worker → API → PostgreSQL 顺序停止子进程')
    try {
      await supervisor?.stop()
    } catch (error) {
      logger?.error(`停止服务失败：${error instanceof Error ? error.message : String(error)}`)
    }
    for (const window of BrowserWindow.getAllWindows()) {
      if (!window.isDestroyed()) window.destroy()
    }
    logger?.info('子进程已回收，应用退出')
  })()
  return shutdownPromise
}

function bootstrap(): void {
  if (!app.requestSingleInstanceLock()) {
    // 第二个实例立即退出，由第一个实例负责聚焦已有窗口。
    app.quit()
    return
  }

  app.on('second-instance', () => focusExistingWindow())
  app.on('window-all-closed', () => app.quit())
  app.on('activate', () => {
    if (mainWindow && !mainWindow.isDestroyed()) {
      focusExistingWindow()
      return
    }
    if (currentStatus?.phase === 'ready') {
      void openMainWindow()
      return
    }
    createShellWindow()
  })

  app.on('before-quit', (event) => {
    if (quitting) return
    event.preventDefault()
    quitting = true
    void shutdown().finally(() => app.exit(0))
  })

  process.once('SIGINT', () => {
    logger?.info('收到 SIGINT，开始优雅退出')
    app.quit()
  })
  process.once('SIGTERM', () => {
    logger?.info('收到 SIGTERM，开始优雅退出')
    app.quit()
  })
  process.on('uncaughtException', (error) => {
    logger?.error(`未捕获异常：${error.stack ?? error.message}`)
  })
  process.on('unhandledRejection', (reason) => {
    logger?.error(`未处理的 Promise 拒绝：${String(reason)}`)
  })

  void app.whenReady().then(() => {
    config = loadConfig(process.env, app.getPath('userData'), app.isPackaged)
    logger = new Logger(config.logDir)
    const stubSuffix = config.stub ? '（测试桩模式）' : ''
    logger.info(
      `Electron ${process.versions.electron} / Chromium ${process.versions.chrome} / Node ${process.versions.node}${stubSuffix}`,
    )
    logger.info(`用户目录：${config.userDataDir}`)
    logger.info(`数据目录：${config.dataDir}`)
    logger.info(`日志目录：${config.logDir}`)
    logger.info(
      config.bootstrap
        ? '启动顺序：bootstrap → API → Worker（首启初始化由 sidecar 负责）'
        : '启动顺序：PostgreSQL → migrate → API → Worker',
    )
    if (config.bootstrap) {
      logger.info(`自动登录：${config.autoLogin ? '启用' : '已关闭（CAMPUSFLOW_AUTO_LOGIN=0）'}`)
    }

    installIpc()
    buildMenu()
    installSecurity({
      targetSession: session.defaultSession,
      rendererDir,
      allowlist: config.externalAllowlist,
      getAppOrigin,
      onBlockedNavigation: (url) => logger?.warn(`已阻止导航或新窗口：${url}`),
    })
    session.defaultSession.on('will-download', (_event, item) => {
      logger?.info(`开始下载：${item.getFilename()}（${item.getTotalBytes()} 字节）`)
      item.on('done', (_doneEvent, state) => {
        logger?.info(`下载结束：${item.getFilename()} 状态 ${state} 路径 ${item.getSavePath()}`)
      })
    })

    supervisor = new Supervisor({ config, logger })
    supervisor.on('status', handleStatus)
    createShellWindow()
    void supervisor.start().catch(() => {
      // 失败详情已通过 status 事件进入错误窗口，这里只避免未处理拒绝。
    })
  })
}

bootstrap()
