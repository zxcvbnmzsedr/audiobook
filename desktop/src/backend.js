const fs = require('node:fs')
const path = require('node:path')
const { spawn } = require('node:child_process')
const { cacheDir, dataDir, logDir, backendDir, backendLaunchRoot } = require('./paths')
const { findAvailablePort } = require('./ports')

const STARTUP_TIMEOUT_MS = 120_000
const HEALTH_INTERVAL_MS = 750
const SHUTDOWN_GRACE_MS = 4_000

let backendProcess = null
let backendStartup = null
let backendState = {
  port: null,
  url: '',
  logs: [],
  ready: false,
  error: '',
  pid: null,
  command: '',
  cwd: '',
  startedAt: '',
  stopping: false,
  mode: 'managed',
  managed: true,
}

function ensureRuntimeDirs() {
  for (const dir of [dataDir(), cacheDir(), logDir()]) {
    fs.mkdirSync(dir, { recursive: true })
  }
}

function appendLog(line) {
  const value = String(line || '').trimEnd()
  if (!value) return
  backendState.logs.push(value)
  if (backendState.logs.length > 1000) {
    backendState.logs.shift()
  }
  try {
    fs.appendFileSync(path.join(logDir(), 'backend.log'), `${value}\n`)
  } catch {
    // Log persistence should never block app startup.
  }
}

function commandExists(command) {
  const check = process.platform === 'win32' ? 'where' : '/bin/sh'
  const args = process.platform === 'win32' ? [command] : ['-lc', `command -v ${command}`]
  const result = spawn(check, args, { shell: false })
  return new Promise((resolve) => {
    result.on('exit', (code) => resolve(code === 0))
    result.on('error', () => resolve(false))
  })
}

function quoteShellArg(value) {
  return `'${String(value).replace(/'/g, "'\\''")}'`
}

function commandLineValue(name) {
  const inline = process.argv.find((arg) => arg.startsWith(`${name}=`))
  if (inline) {
    return inline.slice(name.length + 1)
  }
  const index = process.argv.indexOf(name)
  if (index >= 0) {
    return process.argv[index + 1] || ''
  }
  return ''
}

function normalizeLocalBackendUrl(value) {
  const rawValue = String(value || '').trim()
  if (!rawValue) return ''

  let parsed
  try {
    parsed = new URL(rawValue)
  } catch {
    throw new Error(`Invalid backend URL: ${rawValue}`)
  }

  const allowedHosts = new Set(['127.0.0.1', 'localhost', '::1', '[::1]'])
  if (!['http:', 'https:'].includes(parsed.protocol) || !allowedHosts.has(parsed.hostname)) {
    throw new Error('VOC_STUDIO_BACKEND_URL must point to a local http://127.0.0.1, localhost, or ::1 service.')
  }

  return parsed.origin
}

function configuredExternalBackendUrl() {
  return normalizeLocalBackendUrl(
    process.env.VOC_STUDIO_BACKEND_URL
    || commandLineValue('--backend-url'),
  )
}

async function backendCommand() {
  if (await commandExists('uv')) {
    return {
      command: 'uv',
      args: ['run', 'python', 'app/app.py'],
      shell: false,
      cwd: backendLaunchRoot(),
    }
  }
  return {
    command: process.platform === 'win32' ? 'python' : 'python3',
    args: ['app.py'],
    shell: false,
    cwd: backendDir(),
  }
}

async function waitForHealth(url) {
  const startedAt = Date.now()
  let lastError = ''

  while (Date.now() - startedAt < STARTUP_TIMEOUT_MS) {
    if (backendProcess?.exitCode !== null && backendProcess?.exitCode !== undefined) {
      throw new Error(`Backend exited before becoming ready. ${backendState.error || lastError}`)
    }
    try {
      const response = await fetch(`${url}/api/config`)
      if (response.ok) return
      lastError = `HTTP ${response.status}`
    } catch (error) {
      lastError = error.message
    }
    await new Promise((resolve) => setTimeout(resolve, HEALTH_INTERVAL_MS))
  }

  throw new Error(`Backend startup timed out after ${STARTUP_TIMEOUT_MS / 1000}s. ${lastError}`)
}

async function startBackend() {
  if (backendState.ready && (backendProcess || !backendState.managed)) {
    return backendState
  }
  if (backendStartup) {
    return backendStartup
  }

  backendStartup = launchBackend().finally(() => {
    backendStartup = null
  })
  return backendStartup
}

async function launchBackend() {
  ensureRuntimeDirs()
  const externalUrl = configuredExternalBackendUrl()
  if (externalUrl) {
    return attachExternalBackend(externalUrl)
  }

  return launchManagedBackend()
}

async function attachExternalBackend(url) {
  const parsed = new URL(url)
  backendState = {
    port: Number(parsed.port || (parsed.protocol === 'https:' ? 443 : 80)),
    url,
    logs: [],
    ready: false,
    error: '',
    pid: null,
    command: `VOC_STUDIO_BACKEND_URL=${quoteShellArg(url)} pnpm desktop`,
    cwd: backendLaunchRoot(),
    startedAt: new Date().toISOString(),
    stopping: false,
    mode: 'external',
    managed: false,
  }

  appendLog(`Attaching to external backend: ${url}`)
  await waitForHealth(url)
  backendState.ready = true
  appendLog('External backend is ready.')
  return backendState
}

async function launchManagedBackend() {
  backendState = {
    port: await findAvailablePort(Number(process.env.VOC_STUDIO_PORT || 4200)),
    url: '',
    logs: [],
    ready: false,
    error: '',
    pid: null,
    command: '',
    cwd: '',
    startedAt: new Date().toISOString(),
    stopping: false,
    mode: 'managed',
    managed: true,
  }
  backendState.url = `http://127.0.0.1:${backendState.port}`

  const launch = await backendCommand()
  const env = {
    ...process.env,
    VOC_STUDIO_HOST: '127.0.0.1',
    VOC_STUDIO_PORT: String(backendState.port),
    VOC_STUDIO_DESKTOP: '1',
    VOC_STUDIO_DATA_DIR: dataDir(),
    HF_HOME: process.env.HF_HOME || path.join(cacheDir(), 'huggingface'),
    TRANSFORMERS_CACHE: process.env.TRANSFORMERS_CACHE || path.join(cacheDir(), 'huggingface', 'transformers'),
  }

  backendState.command = [launch.command, ...launch.args].join(' ')
  backendState.cwd = launch.cwd

  appendLog(`Starting backend: ${backendState.command}`)
  appendLog(`Backend cwd: ${launch.cwd}`)
  appendLog(`Data dir: ${env.VOC_STUDIO_DATA_DIR}`)
  appendLog(`URL: ${backendState.url}`)

  backendProcess = spawn(launch.command, launch.args, {
    cwd: launch.cwd,
    env,
    shell: launch.shell,
    detached: process.platform !== 'win32',
    stdio: ['ignore', 'pipe', 'pipe'],
  })
  backendState.pid = backendProcess.pid ?? null

  backendProcess.stdout.on('data', (data) => appendLog(data.toString()))
  backendProcess.stderr.on('data', (data) => appendLog(data.toString()))
  backendProcess.on('error', (error) => {
    backendState.error = error.message
    appendLog(`Backend process error: ${error.message}`)
  })
  backendProcess.on('exit', (code, signal) => {
    backendState.ready = false
    backendState.pid = null
    if (backendState.stopping) {
      backendState.error = ''
      appendLog(`Backend stopped with code ${code ?? 'null'} signal ${signal ?? 'null'}`)
    } else {
      backendState.error = `Backend exited with code ${code ?? 'null'} signal ${signal ?? 'null'}`
      appendLog(backendState.error)
    }
    backendProcess = null
  })

  await waitForHealth(backendState.url)
  backendState.ready = true
  appendLog('Backend is ready.')
  return backendState
}

function stopBackend() {
  if (!backendState.managed) return
  if (!backendProcess) return
  appendLog('Stopping backend...')
  backendState.stopping = true
  const pid = backendProcess.pid
  if (!pid) {
    backendProcess.kill()
    return
  }
  if (process.platform === 'win32') {
    spawn('taskkill', ['/pid', String(pid), '/t'])
    setTimeout(() => {
      if (backendProcess) {
        spawn('taskkill', ['/pid', String(pid), '/f', '/t'])
      }
    }, SHUTDOWN_GRACE_MS).unref()
  } else {
    try {
      process.kill(-pid, 'SIGTERM')
    } catch {
      backendProcess.kill('SIGTERM')
    }
    setTimeout(() => {
      if (!backendProcess) return
      try {
        process.kill(-pid, 'SIGKILL')
      } catch {
        backendProcess.kill('SIGKILL')
      }
    }, SHUTDOWN_GRACE_MS).unref()
  }
  backendState.ready = false
}

function openBackendInTerminal() {
  const state = getBackendState()
  if (!state.managed && state.url) {
    return `cd ${quoteShellArg(state.cwd || backendLaunchRoot())} && VOC_STUDIO_BACKEND_URL=${quoteShellArg(state.url)} pnpm desktop`
  }

  const envPrefix = [
    `VOC_STUDIO_HOST=127.0.0.1`,
    `VOC_STUDIO_PORT=${state.port || 4200}`,
    `VOC_STUDIO_DESKTOP=1`,
    `VOC_STUDIO_DATA_DIR=${quoteShellArg(dataDir())}`,
    `HF_HOME=${quoteShellArg(path.join(cacheDir(), 'huggingface'))}`,
    `TRANSFORMERS_CACHE=${quoteShellArg(path.join(cacheDir(), 'huggingface', 'transformers'))}`,
  ].join(' ')
  return `cd ${quoteShellArg(state.cwd || backendLaunchRoot())} && ${envPrefix} ${state.command || 'uv run python app/app.py'}`
}

function getBackendState() {
  return {
    ...backendState,
    dataDir: dataDir(),
    cacheDir: cacheDir(),
    logDir: logDir(),
  }
}

module.exports = {
  getBackendState,
  openBackendInTerminal,
  startBackend,
  stopBackend,
}
