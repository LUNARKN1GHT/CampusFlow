import path from 'node:path'

import type { Logger } from './logger'
import type { RunResult } from './types'

/**
 * 数据维护入口：`.campusflow` 导出／导入与数据库升级。
 * 全部动作都复用 sidecar CLI，不在 Electron 里复制校验、备份或迁移逻辑。
 * 导入与升级会替换数据库，因此先暂停 API 与 Worker（PostgreSQL 保持运行），
 * 成功后重启应用；失败则恢复服务，不留下半停止状态。
 */

export interface DialogFileResult {
  canceled: boolean
  filePath?: string
  filePaths?: string[]
}

export interface DialogMessageOptions {
  type: 'none' | 'info' | 'error' | 'question' | 'warning'
  title: string
  message: string
  detail?: string
  buttons?: string[]
  defaultId?: number
  cancelId?: number
}

export interface DialogPort {
  showSaveDialog(options: {
    title: string
    defaultPath: string
    filters: { name: string; extensions: string[] }[]
  }): Promise<DialogFileResult>
  showOpenDialog(options: {
    title: string
    filters: { name: string; extensions: string[] }[]
    properties: ('openFile' | 'dontAddToRecent')[]
  }): Promise<DialogFileResult>
  showMessageBox(options: DialogMessageOptions): Promise<{ response: number }>
}

export interface MaintenanceContext {
  dialogs: DialogPort
  logger: Logger
  run: (subcommand: string, extra: string[], timeoutMs: number) => Promise<RunResult>
  suspendServices: () => Promise<void>
  resumeServices: () => Promise<void>
  relaunch: () => void
  defaultArchivePath: () => string
}

export type MaintenanceOutcome = 'done' | 'cancelled' | 'failed'

const ARCHIVE_FILTER = { name: 'CampusFlow 归档', extensions: ['campusflow'] }
const EXPORT_TIMEOUT_MS = 600_000
const IMPORT_TIMEOUT_MS = 900_000
const INSPECT_TIMEOUT_MS = 300_000
const UPGRADE_TIMEOUT_MS = 900_000

/** 归档文件名固定为本地时间戳，避免同名覆盖；不含任何主机或账号信息。 */
export function defaultArchiveName(now: Date = new Date()): string {
  const pad = (value: number): string => String(value).padStart(2, '0')
  const stamp =
    `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}` +
    `-${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`
  return `campusflow-${stamp}.campusflow`
}

/** 只接受完整 JSON 对象；混入日志的输出一律当作失败，不从文本里猜字段。 */
export function parseJsonOutput(stdout: string): Record<string, unknown> | null {
  const text = stdout.trim()
  if (!text) return null
  try {
    const value: unknown = JSON.parse(text)
    if (!value || typeof value !== 'object' || Array.isArray(value)) return null
    return value as Record<string, unknown>
  } catch {
    return null
  }
}

export function formatBytes(value: unknown): string {
  const bytes = typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : 0
  if (bytes < 1024) return `${bytes} B`
  const units = ['KB', 'MB', 'GB', 'TB']
  let size = bytes / 1024
  let index = 0
  while (size >= 1024 && index < units.length - 1) {
    size /= 1024
    index += 1
  }
  return `${size.toFixed(1)} ${units[index]}`
}

function textField(payload: Record<string, unknown>, name: string): string {
  const value = payload[name]
  return typeof value === 'string' && value.length > 0 ? value : ''
}

function countSummary(value: unknown): string {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return ''
  const entries = Object.entries(value as Record<string, unknown>)
    .filter(([, count]) => typeof count === 'number' && count > 0)
    .map(([key, count]) => `${key} ${count}`)
  return entries.length > 0 ? entries.join('、') : '（无业务数据）'
}

/** 失败提示只展示可读摘要，不把完整 stderr（可能很长）塞进弹窗。 */
function failureDetail(result: RunResult): string {
  const detail = `${result.stderr}`.trim().split(/\r?\n/).filter(Boolean).slice(-6).join('\n')
  const code = result.errorCode ? `（${result.errorCode}）` : `（退出码 ${result.code}）`
  return `退出码${code}${detail ? `\n${detail}` : ''}`
}

async function showFailure(
  ctx: MaintenanceContext,
  title: string,
  result: RunResult,
): Promise<void> {
  ctx.logger.error(`${title}：${failureDetail(result)}`)
  await ctx.dialogs.showMessageBox({
    type: 'error',
    title,
    message: title,
    detail: `${failureDetail(result)}\n\n日志目录可在「CampusFlow → 打开日志目录」中找到。`,
    buttons: ['知道了'],
  })
}

export async function exportArchive(ctx: MaintenanceContext): Promise<MaintenanceOutcome> {
  const target = await ctx.dialogs.showSaveDialog({
    title: '导出 CampusFlow 数据',
    defaultPath: ctx.defaultArchivePath(),
    filters: [ARCHIVE_FILTER],
  })
  if (target.canceled || !target.filePath) return 'cancelled'
  const output = target.filePath.toLowerCase().endsWith('.campusflow')
    ? target.filePath
    : `${target.filePath}.campusflow`

  ctx.logger.info(`开始导出数据归档：${output}`)
  const result = await ctx.run('export', ['--output', output], EXPORT_TIMEOUT_MS)
  const payload = parseJsonOutput(result.stdout)
  if (result.code !== 0 || !payload) {
    await showFailure(ctx, '导出失败', result)
    return 'failed'
  }
  ctx.logger.info(`数据归档导出完成：${output}`)
  const lines = [
    `文件：${textField(payload, 'path') || output}`,
    `大小：${formatBytes(payload.size)}`,
    `SHA-256：${textField(payload, 'sha256') || '（未提供）'}`,
    '',
    '归档包含数据库与资料文件，不包含模型密钥与外部日历授权。',
    '请把文件保存在安全位置；导入到另一台电脑前需先退出 CampusFlow。',
  ]
  const excluded = Array.isArray(payload.excluded_credentials) ? payload.excluded_credentials : []
  if (excluded.length > 0) lines.splice(4, 0, `已排除凭据：${excluded.length} 项`)
  await ctx.dialogs.showMessageBox({
    type: 'info',
    title: '导出完成',
    message: 'CampusFlow 数据已导出',
    detail: lines.join('\n'),
    buttons: ['完成'],
  })
  return 'done'
}

export async function importArchive(ctx: MaintenanceContext): Promise<MaintenanceOutcome> {
  const picked = await ctx.dialogs.showOpenDialog({
    title: '选择 CampusFlow 归档',
    filters: [ARCHIVE_FILTER],
    properties: ['openFile'],
  })
  const archive = picked.filePaths?.[0]
  if (picked.canceled || !archive) return 'cancelled'

  ctx.logger.info(`检查导入归档：${archive}`)
  const inspected = await ctx.run('import', [archive, '--inspect'], INSPECT_TIMEOUT_MS)
  const info = parseJsonOutput(inspected.stdout)
  if (inspected.code !== 0 || !info) {
    await showFailure(ctx, '无法读取归档', inspected)
    return 'failed'
  }
  const token = textField(info, 'confirm_token')
  if (!token) {
    await showFailure(ctx, '归档缺少确认令牌', inspected)
    return 'failed'
  }

  const lines = [
    `归档：${textField(info, 'path') || archive}`,
    `创建时间：${textField(info, 'created_at') || '未知'}`,
    `来源：${textField(info, 'platform') || '未知平台'}，CampusFlow ${textField(info, 'app_version') || '未知版本'}`,
    `数据库结构：${textField(info, 'schema_head') || '未知'}`,
    `数据库条目：${countSummary(info.counts)}`,
    `资料文件：${typeof info.material_count === 'number' ? info.material_count : '未知'} 个（${formatBytes(info.material_bytes)}）`,
  ]
  if (info.requires_migration === true) {
    lines.push(`升级提示：${textField(info, 'migration_message') || '导入后会升级数据库结构'}`)
  }
  const reauth = Array.isArray(info.requires_reauthorization)
    ? info.requires_reauthorization.filter((item): item is string => typeof item === 'string')
    : []
  if (reauth.length > 0) lines.push(`需要重新授权：${reauth.join('、')}`)
  lines.push('', '导入会整体替换本机现有数据（导入前会自动创建安全备份）；成功后应用将重新启动。')

  const answer = await ctx.dialogs.showMessageBox({
    type: 'warning',
    title: '导入 CampusFlow 数据',
    message: '确认用该归档替换本机数据？',
    detail: lines.join('\n'),
    buttons: ['取消', '导入并重启'],
    defaultId: 0,
    cancelId: 0,
  })
  if (answer.response !== 1) return 'cancelled'

  await ctx.suspendServices()
  ctx.logger.info('已暂停 API 与 Worker，开始导入')
  const result = await ctx.run(
    'import',
    [archive, '--replace', '--confirm-token', token],
    IMPORT_TIMEOUT_MS,
  )
  const payload = parseJsonOutput(result.stdout)
  if (result.code !== 0 || !payload) {
    await ctx.resumeServices()
    await showFailure(ctx, '导入失败', result)
    return 'failed'
  }

  ctx.logger.info(`导入完成：${archive}`)
  const backup = textField(payload, 'backup_archive')
  const detail = [
    `已恢复条目：${countSummary(payload.restored_counts)}`,
    backup ? `导入前安全备份：${backup}` : '',
    reauth.length > 0 ? `需要重新连接：${reauth.join('、')}` : '',
    '',
    '应用即将重新启动。',
  ]
    .filter(Boolean)
    .join('\n')
  await ctx.dialogs.showMessageBox({
    type: 'info',
    title: '导入完成',
    message: '数据已导入，应用将重新启动',
    detail,
    buttons: ['立即重启'],
  })
  ctx.relaunch()
  return 'done'
}

export async function checkAndApplyUpgrade(
  ctx: MaintenanceContext,
): Promise<MaintenanceOutcome> {
  ctx.logger.info('检查数据库升级状态')
  const checked = await ctx.run('upgrade', ['--check', '--json'], INSPECT_TIMEOUT_MS)
  const payload = parseJsonOutput(checked.stdout)
  if (!payload) {
    await showFailure(ctx, '无法检查升级状态', checked)
    return 'failed'
  }
  const state = textField(payload, 'state')
  const current = textField(payload, 'database_revision') || '（空）'
  const head = textField(payload, 'bundled_head') || '未知'

  if (state === 'up_to_date') {
    await ctx.dialogs.showMessageBox({
      type: 'info',
      title: '数据库已是最新',
      message: '无需升级',
      detail: `当前结构：${current}\n内置版本：${head}`,
      buttons: ['完成'],
    })
    return 'done'
  }
  if (state === 'uninitialized') {
    await ctx.dialogs.showMessageBox({
      type: 'info',
      title: '数据库尚未初始化',
      message: '请先正常启动 CampusFlow 完成首启初始化',
      buttons: ['知道了'],
    })
    return 'cancelled'
  }
  if (state === 'database_ahead') {
    await ctx.dialogs.showMessageBox({
      type: 'error',
      title: '数据库版本过新',
      message: '本机数据库由更新版本的 CampusFlow 创建',
      detail: `当前结构：${current}\n内置版本：${head}\n\n本版本不会降级数据库，请安装与原数据库版本相同或更新的 CampusFlow。`,
      buttons: ['知道了'],
    })
    return 'failed'
  }
  if (state !== 'upgrade_required') {
    await showFailure(ctx, '升级状态无法识别', checked)
    return 'failed'
  }

  const answer = await ctx.dialogs.showMessageBox({
    type: 'warning',
    title: '升级数据库结构',
    message: `需要升级数据库：${current} → ${head}`,
    detail:
      '升级前会先做完整备份，并在失败时自动恢复。\n' +
      '升级期间 API 与后台任务会暂停，成功后应用将重新启动。',
    buttons: ['取消', '备份并升级'],
    defaultId: 0,
    cancelId: 0,
  })
  if (answer.response !== 1) return 'cancelled'

  await ctx.suspendServices()
  ctx.logger.info('已暂停 API 与 Worker，开始升级数据库')
  const applied = await ctx.run(
    'upgrade',
    ['--check', '--apply', '--json'],
    UPGRADE_TIMEOUT_MS,
  )
  const appliedPayload = parseJsonOutput(applied.stdout)
  if (applied.code !== 0 || !appliedPayload) {
    await ctx.resumeServices()
    await showFailure(ctx, '升级失败', applied)
    return 'failed'
  }

  ctx.logger.info('数据库升级完成')
  const backup = textField(appliedPayload, 'backup')
  await ctx.dialogs.showMessageBox({
    type: 'info',
    title: '升级完成',
    message: '数据库结构已升级，应用将重新启动',
    detail: [
      `升级后结构：${textField(appliedPayload, 'bundled_head') || head}`,
      backup ? `升级前备份：${backup}` : '',
      '',
      '应用即将重新启动。',
    ]
      .filter(Boolean)
      .join('\n'),
    buttons: ['立即重启'],
  })
  ctx.relaunch()
  return 'done'
}

/** 默认导出位置：用户文稿目录下的时间戳文件名，仍可在保存对话框中修改。 */
export function resolveDefaultArchivePath(documentsDir: string, now: Date = new Date()): string {
  return path.join(documentsDir, defaultArchiveName(now))
}
