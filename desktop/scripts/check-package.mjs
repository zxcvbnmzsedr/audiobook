import { existsSync, readdirSync, statSync } from 'node:fs'
import { basename, join, relative } from 'node:path'

const releaseDir = join(process.cwd(), 'release')
const forbiddenNames = new Set(['config.json', 'books', 'uploads', '.venv', '.uv-cache'])
const forbiddenSuffixes = ['.pyc']
const requiredResources = [
  'app/app.py',
  'app/static/index.html',
  'builtin_lora/manifest.json',
  'default_prompts.txt',
  'review_prompts.txt',
  'pyproject.toml',
  'uv.lock',
]

function findPackageResourceDirs(root, results = []) {
  if (!existsSync(root)) return results

  for (const entry of readdirSync(root)) {
    const fullPath = join(root, entry)
    const stats = statSync(fullPath)
    if (!stats.isDirectory()) continue

    if (basename(fullPath).toLowerCase() === 'resources' && (
      existsSync(join(fullPath, 'app.asar'))
      || existsSync(join(fullPath, 'app'))
      || existsSync(join(fullPath, 'pyproject.toml'))
    )) {
      results.push(fullPath)
      continue
    }

    findPackageResourceDirs(fullPath, results)
  }

  return results
}

function walk(packageResources, dir, results = []) {
  for (const entry of readdirSync(dir)) {
    const fullPath = join(dir, entry)
    const relativePath = relative(packageResources, fullPath)
    const stats = statSync(fullPath)
    if (forbiddenNames.has(entry) || forbiddenSuffixes.some((suffix) => entry.endsWith(suffix))) {
      results.push(relativePath)
    }
    if (stats.isDirectory()) {
      walk(packageResources, fullPath, results)
    }
  }
  return results
}

const packageResourceDirs = findPackageResourceDirs(releaseDir)

if (packageResourceDirs.length === 0) {
  console.error(`Package resources not found under: ${releaseDir}`)
  console.error('Run `npm --prefix desktop run pack -- --publish never` first.')
  process.exit(1)
}

const failures = []

for (const packageResources of packageResourceDirs) {
  const matches = walk(packageResources, packageResources)
  if (matches.length > 0) {
    failures.push(`Forbidden runtime files were packaged in ${packageResources}:`)
    matches.forEach((match) => failures.push(`- ${match}`))
  }

  const missing = requiredResources.filter((relativePath) => !existsSync(join(packageResources, relativePath)))
  if (missing.length > 0) {
    failures.push(`Required package resources are missing in ${packageResources}:`)
    missing.forEach((match) => failures.push(`- ${match}`))
  }
}

if (failures.length > 0) {
  failures.forEach((failure) => console.error(failure))
  process.exit(1)
}

console.log('Package resources clean:')
packageResourceDirs.forEach((packageResources) => console.log(`- ${packageResources}`))
