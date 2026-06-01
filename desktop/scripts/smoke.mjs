import { createServer } from 'node:net'
import { existsSync, readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const require = createRequire(import.meta.url)
const { findAvailablePort } = require('../src/ports.js')

const desktopRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const repoRoot = resolve(desktopRoot, '..')
const failures = []

function assert(condition, message) {
  if (!condition) failures.push(message)
}

function readDesktopFile(relativePath) {
  return readFileSync(join(desktopRoot, relativePath), 'utf8')
}

function assertSourceIncludes(source, text, message) {
  assert(source.includes(text), message)
}

function assertSourceMatches(source, pattern, message) {
  assert(pattern.test(source), message)
}

function listen(host, port) {
  return new Promise((resolve, reject) => {
    const server = createServer()
    server.once('error', reject)
    server.listen(port, host, () => resolve(server))
  })
}

function close(server) {
  return new Promise((resolve, reject) => {
    server.close((error) => {
      if (error) reject(error)
      else resolve()
    })
  })
}

async function checkPortSelection() {
  const host = '127.0.0.1'
  const startPort = await findAvailablePort(43000, host)
  const blocker = await listen(host, startPort)

  try {
    const selectedPort = await findAvailablePort(startPort, host)
    assert(selectedPort > startPort, `findAvailablePort returned occupied port ${selectedPort}.`)
    assert(selectedPort < startPort + 100, `findAvailablePort returned port ${selectedPort} outside scan range.`)

    const verifier = await listen(host, selectedPort)
    await close(verifier)
  } finally {
    await close(blocker)
  }
}

function checkPackageConfig() {
  const packageJson = JSON.parse(readDesktopFile('package.json'))

  assert(packageJson.name === 'voc-studio-desktop', 'desktop package name must stay voc-studio-desktop.')
  assert(packageJson.main === 'src/main.js', 'desktop main entry must be src/main.js.')
  assert(packageJson.build?.appId === 'studio.voc.app', 'Electron appId must be studio.voc.app.')
  assert(packageJson.build?.productName === 'Voc Studio', 'Electron productName must be Voc Studio.')
  assert(packageJson.scripts?.check?.includes('check:smoke'), 'npm run check must include check:smoke.')
  assert(packageJson.scripts?.check?.includes('check:package'), 'npm run check must include packaged resource validation.')

  for (const file of ['src/**/*', 'assets/**/*', 'package.json']) {
    assert(packageJson.build?.files?.includes(file), `Electron package files must include ${file}.`)
  }

  const resourceTargets = new Set((packageJson.build?.extraResources ?? []).map((entry) => entry.to))
  for (const target of ['app', 'default_prompts.txt', 'review_prompts.txt', 'builtin_lora/manifest.json', 'pyproject.toml', 'uv.lock']) {
    assert(resourceTargets.has(target), `Electron extraResources must include ${target}.`)
  }
}

function checkSourceContracts() {
  const main = readDesktopFile('src/main.js')
  const backend = readDesktopFile('src/backend.js')
  const preload = readDesktopFile('src/preload.js')

  assertSourceIncludes(main, "app.setName('Voc Studio')", 'Electron app name must be Voc Studio.')
  assertSourceIncludes(main, "title: 'Voc Studio'", 'Main window title must be Voc Studio.')
  assertSourceIncludes(main, 'app.requestSingleInstanceLock()', 'Electron app must enforce a single instance.')
  assertSourceIncludes(main, 'VOC_STUDIO_EXIT_AFTER_LOAD', 'Electron launch smoke must be able to quit after the first page load.')
  assertSourceMatches(main, /contextIsolation:\s*true/, 'Renderer must keep contextIsolation enabled.')
  assertSourceMatches(main, /nodeIntegration:\s*false/, 'Renderer must keep nodeIntegration disabled.')
  assertSourceMatches(main, /sandbox:\s*true/, 'Renderer must keep sandbox enabled.')
  assertSourceIncludes(main, 'setWindowOpenHandler', 'External window opens must be intercepted.')
  assertSourceIncludes(main, 'will-navigate', 'Renderer navigation must be guarded.')
  assertSourceIncludes(main, 'setPermissionRequestHandler', 'Permission requests must be handled explicitly.')
  assertSourceIncludes(main, 'callback(false)', 'Permission requests must default to denied.')
  assertSourceIncludes(main, 'shell.openExternal', 'External URLs must be opened by the system browser.')

  assertSourceIncludes(backend, 'VOC_STUDIO_BACKEND_URL', 'Backend launcher must support attaching to an existing backend URL.')
  assertSourceIncludes(backend, "process.env.VOC_STUDIO_BACKEND_URL", 'Backend launcher must support the external backend URL env var.')
  assertSourceIncludes(backend, "commandLineValue('--backend-url')", 'Backend launcher must support --backend-url.')
  assertSourceIncludes(backend, 'function normalizeLocalBackendUrl', 'External backend URLs must be normalized and validated.')
  assertSourceIncludes(backend, "mode: 'external'", 'External backend state must be visible to diagnostics.')
  assertSourceIncludes(backend, 'managed: false', 'External backend state must be marked unmanaged.')
  assertSourceIncludes(backend, 'backendState.ready && (backendProcess || !backendState.managed)', 'External backend startup must be reused once ready.')
  assertSourceIncludes(backend, 'if (!backendState.managed) return', 'Stopping the Electron app must not stop unmanaged external backends.')
  assertSourceIncludes(backend, 'findAvailablePort(Number(process.env.VOC_STUDIO_PORT || 4200))', 'Backend startup must find an available port from 4200.')
  assertSourceIncludes(backend, 'VOC_STUDIO_DESKTOP', 'Backend environment must mark desktop mode.')
  assertSourceIncludes(backend, 'VOC_STUDIO_DATA_DIR', 'Backend environment must set desktop data directory.')
  assertSourceIncludes(backend, 'HF_HOME', 'Backend environment must set Hugging Face cache directory.')
  assertSourceIncludes(backend, 'TRANSFORMERS_CACHE', 'Backend environment must set transformers cache directory.')
  assertSourceIncludes(backend, "process.kill(-pid, 'SIGTERM')", 'Backend shutdown must target the spawned process group on Unix.')

  assertSourceIncludes(preload, "contextBridge.exposeInMainWorld('vocStudio'", 'Preload must expose the vocStudio bridge.')
  for (const apiName of [
    'backendStatus',
    'copyBackendLaunchCommand',
    'copyDiagnostics',
    'diagnostics',
    'openBackendUrl',
    'openCacheDirectory',
    'openDataDirectory',
    'openLogDirectory',
  ]) {
    assertSourceIncludes(preload, `${apiName}:`, `Preload bridge must expose ${apiName}.`)
  }
}

function checkRepoResources() {
  for (const relativePath of [
    'app/app.py',
    'app/static/index.html',
    'builtin_lora/manifest.json',
    'default_prompts.txt',
    'review_prompts.txt',
    'pyproject.toml',
    'uv.lock',
  ]) {
    assert(existsSync(join(repoRoot, relativePath)), `Required desktop package input is missing: ${relativePath}.`)
  }
}

try {
  checkPackageConfig()
  checkSourceContracts()
  checkRepoResources()
  await checkPortSelection()
} catch (error) {
  failures.push(error.stack || error.message)
}

if (failures.length > 0) {
  console.error('Desktop smoke checks failed:')
  failures.forEach((failure) => console.error(`- ${failure}`))
  process.exit(1)
}

console.log('Desktop smoke checks passed.')
