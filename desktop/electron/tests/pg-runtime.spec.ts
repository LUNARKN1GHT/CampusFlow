import { mkdir, mkdtemp, readFile, readdir, rm, writeFile } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { expect, test } from '@playwright/test'
import { preparePgRuntime } from '../src/pg-runtime'

let root: string
let source: string
let cacheRoot: string
const files = [
  'bin/initdb.exe', 'bin/postgres.exe', 'bin/pg_ctl.exe', 'bin/pg_dump.exe',
  'bin/pg_restore.exe', 'lib/extension.dll', 'share/postgres.bki', 'share/extension/vector.sql',
  'server_license.txt',
]

test.beforeEach(async () => {
  root = await mkdtemp(path.join(os.tmpdir(), 'campusflow-pg-runtime-'))
  source = path.join(root, '中文安装目录', 'pgsql')
  cacheRoot = path.join(root, 'runtime')
  for (const name of [...files, 'pgdata/private-course-data']) {
    const file = path.join(source, name)
    await mkdir(path.dirname(file), { recursive: true })
    await writeFile(file, `synthetic:${name}`)
  }
})
test.afterEach(async () => {
  if (path.dirname(root) !== os.tmpdir() || !path.basename(root).startsWith('campusflow-pg-runtime-')) {
    throw new Error('拒绝清理测试目录之外的路径')
  }
  await rm(root, { recursive: true, force: true })
})
function prepare() {
  return preparePgRuntime({ pgBin: path.join(source, 'bin'), cacheRoot, platform: 'win32' })
}

test('中文安装路径复制必要程序并核对；课程数据不复制、不搬动，重启复用', async () => {
  const bin = await prepare()
  expect(bin).not.toMatch(/[^\x20-\x7e]/)
  for (const file of files) {
    expect(await readFile(path.join(bin, '..', file), 'utf8')).toBe(`synthetic:${file}`)
  }
  expect(await readFile(path.join(source, 'pgdata/private-course-data'), 'utf8')).toContain('synthetic')
  await expect(readFile(path.join(bin, '../pgdata/private-course-data'))).rejects.toThrow()
  expect(await prepare()).toBe(bin)
})

test('英文路径和其他平台无需副本', async () => {
  const bin = path.join(root, 'english', 'bin')
  expect(await preparePgRuntime({ pgBin: bin, cacheRoot: null, platform: 'win32' })).toBe(bin)
  const chinese = path.join(source, 'bin')
  expect(await preparePgRuntime({ pgBin: chinese, cacheRoot: null, platform: 'darwin' })).toBe(chinese)
})

test('程序文件更新时使用新副本，不覆盖旧版本', async () => {
  const old = await prepare()
  await writeFile(path.join(source, 'share/extension/vector.sql'), 'new-version')
  const current = await prepare()
  expect(current).not.toBe(old)
  expect(await readFile(path.join(old, '../share/extension/vector.sql'), 'utf8')).toContain('synthetic')
  expect(await readFile(path.join(current, '../share/extension/vector.sql'), 'utf8')).toBe('new-version')
})

test('损坏副本及遗留临时目录不会被使用', async () => {
  const broken = await prepare()
  await writeFile(path.join(broken, 'postgres.exe'), 'damaged')
  await mkdir(path.join(cacheRoot, '.prepare-interrupted'))
  const repaired = await prepare()
  expect(repaired).not.toBe(broken)
  expect(await readFile(path.join(repaired, 'postgres.exe'), 'utf8')).toBe('synthetic:bin/postgres.exe')
  expect(await prepare()).toBe(repaired)
})

test('并发准备只返回完整副本', async () => {
  const results = await Promise.all([prepare(), prepare()])
  for (const bin of results) {
    expect(await readFile(path.join(bin, '../share/postgres.bki'), 'utf8')).toBe('synthetic:share/postgres.bki')
  }
  expect((await readdir(cacheRoot)).filter(name => name.startsWith('.prepare-'))).toEqual([])
})

test('原始安装不完整时明确报错，不能发布可执行副本', async () => {
  await rm(path.join(source, 'share/postgres.bki'))
  await expect(prepare()).rejects.toThrow('缺少 share/postgres.bki')
  expect(await readdir(cacheRoot)).toEqual([])
})

test('缓存路径含中文或未提供时说明修复办法', async () => {
  for (const invalid of [null, path.join(root, '中文缓存'), 'relative-cache']) {
    await expect(preparePgRuntime({ pgBin: path.join(source, 'bin'), cacheRoot: invalid,
      platform: 'win32' })).rejects.toThrow('CAMPUSFLOW_PG_RUNTIME_DIR')
  }
})

test('缓存不可写入时保留原始运行文件', async () => {
  await writeFile(cacheRoot, 'not-a-directory')
  await expect(prepare()).rejects.toThrow()
  expect(await readFile(path.join(source, 'bin/postgres.exe'), 'utf8')).toBe('synthetic:bin/postgres.exe')
})
