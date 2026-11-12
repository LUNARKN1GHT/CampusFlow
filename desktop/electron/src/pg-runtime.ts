import { createHash, randomUUID } from 'node:crypto'
import { createReadStream } from 'node:fs'
import { copyFile, lstat, mkdir, mkdtemp, readdir, realpath, rename, rm } from 'node:fs/promises'
import path from 'node:path'

const ascii = (value: string) => /^[\x20-\x7e]+$/.test(value)
const required = [
  'bin/initdb.exe', 'bin/postgres.exe', 'bin/pg_ctl.exe', 'bin/pg_dump.exe',
  'bin/pg_restore.exe', 'share/postgres.bki',
]
type Entry = { name: string; digest: string }

async function fingerprint(root: string): Promise<Entry[]> {
  const entries: Entry[] = []
  async function visit(relative: string): Promise<void> {
    const file = path.join(root, relative)
    const stat = await lstat(file)
    if (stat.isSymbolicLink()) throw new Error('数据库运行文件不能包含链接目录或链接文件')
    if (stat.isDirectory()) {
      for (const name of (await readdir(file)).sort()) await visit(`${relative}/${name}`)
    } else if (stat.isFile()) {
      const hash = createHash('sha256')
      for await (const chunk of createReadStream(file)) hash.update(chunk)
      entries.push({ name: relative, digest: hash.digest('hex') })
    } else {
      throw new Error('数据库运行目录包含不支持的文件类型')
    }
  }
  // 只读取程序目录；即使开发环境把 pgdata 放在同一层，也不会复制用户数据。
  for (const name of ['bin', 'lib', 'share']) await visit(name)
  for (const name of ['server_license.txt', 'commandlinetools_3rd_party_licenses.txt']) {
    try { await lstat(path.join(root, name)) } catch (error) {
      if ((error as NodeJS.ErrnoException).code === 'ENOENT') continue
      throw error
    }
    await visit(name)
  }
  for (const name of required) {
    if (!entries.some(entry => entry.name === name)) throw new Error(`数据库运行文件不完整：缺少 ${name}`)
  }
  return entries
}

function manifest(entries: Entry[]): string {
  return JSON.stringify(entries)
}

/** 中文安装路径只改变运行程序位置，绝不改变数据库、资料或备份目录。 */
export async function preparePgRuntime(options: {
  pgBin: string
  cacheRoot: string | null | undefined
  platform?: NodeJS.Platform
  progress?: (message: string) => void
}): Promise<string> {
  if ((options.platform ?? process.platform) !== 'win32') return options.pgBin
  if (ascii(options.pgBin)) return options.pgBin
  const sourceBin = await realpath(options.pgBin)
  const root = options.cacheRoot
  if (!root || !path.isAbsolute(root) || !ascii(root)) {
    throw new Error('无法准备数据库运行副本：请将 CAMPUSFLOW_PG_RUNTIME_DIR 设置为可写的全英文绝对路径；课程数据无需搬动')
  }
  const source = path.dirname(sourceBin)
  const relative = path.relative(source, path.resolve(root))
  if (!relative || (!relative.startsWith(`..${path.sep}`) && relative !== '..' && !path.isAbsolute(relative))) {
    throw new Error('数据库运行副本目录不能位于原程序目录内')
  }
  await mkdir(root, { recursive: true })
  const cacheRoot = await realpath(root)
  if (!ascii(cacheRoot)) throw new Error('数据库运行副本目录实际指向中文路径，请改用全英文目录')
  const actualRelative = path.relative(source, cacheRoot)
  if (!actualRelative || (!actualRelative.startsWith(`..${path.sep}`) && actualRelative !== '..' && !path.isAbsolute(actualRelative))) {
    throw new Error('数据库运行副本目录不能指向原程序目录内')
  }
  options.progress?.('正在核对数据库运行文件；课程资料仍保留在原位置…')
  const entries = await fingerprint(source)
  const expected = manifest(entries)
  const digest = createHash('sha256').update(expected).digest('hex')
  // 每份副本完整核对内容；损坏或中断的副本不执行、不原地覆盖。
  for (const candidate of (await readdir(cacheRoot)).sort()) {
    if (!candidate.startsWith(`${digest}-`)) continue
    const destination = path.join(cacheRoot, candidate)
    try {
      if ((await lstat(destination)).isSymbolicLink()) continue
      if (manifest(await fingerprint(destination)) === expected) {
        return path.join(destination, 'bin')
      }
    } catch { /* 不完整副本跳过，重新准备。 */ }
  }
  options.progress?.('正在准备英文路径中的数据库运行副本，首次启动需要稍等…')
  const staging = await mkdtemp(path.join(cacheRoot, '.prepare-'))
  try {
    for (const entry of entries) {
      const destination = path.join(staging, entry.name)
      await mkdir(path.dirname(destination), { recursive: true })
      await copyFile(path.join(source, entry.name), destination)
    }
    // 保留可能为空的必要目录。
    for (const name of ['bin', 'lib', 'share']) await mkdir(path.join(staging, name), { recursive: true })
    if (manifest(await fingerprint(staging)) !== expected) {
      throw new Error('数据库运行副本校验失败，请重试启动')
    }
    // 唯一目录名允许并发启动安全发布；不覆盖正在被其他实例使用的副本。
    const destination = path.join(cacheRoot, `${digest}-${randomUUID()}`)
    await rename(staging, destination)
    return path.join(destination, 'bin')
  } finally {
    // 只清理本次创建且位于缓存根目录中的临时文件夹。
    if (path.dirname(staging) === cacheRoot && path.basename(staging).startsWith('.prepare-')) {
      await rm(staging, { recursive: true, force: true })
    }
  }
}
