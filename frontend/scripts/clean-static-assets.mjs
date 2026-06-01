import { rm, mkdir } from 'node:fs/promises'
import { resolve } from 'node:path'

const assetsDir = resolve(import.meta.dirname, '../../app/static/assets')

await rm(assetsDir, { recursive: true, force: true })
await mkdir(assetsDir, { recursive: true })
