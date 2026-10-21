import { rm } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')

for (const name of ['dist', 'test-results', 'playwright-report']) {
  await rm(path.join(root, name), { recursive: true, force: true })
}
