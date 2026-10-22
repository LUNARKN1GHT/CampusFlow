import { cp, mkdir } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const source = path.join(root, 'src', 'renderer')
const target = path.join(root, 'dist', 'renderer')

await mkdir(target, { recursive: true })
await cp(source, target, { recursive: true })
console.log(`renderer copied: ${path.relative(root, target)}`)
