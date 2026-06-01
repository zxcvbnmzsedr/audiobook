import { createServer } from 'node:http'
import { spawn } from 'node:child_process'
import { createRequire } from 'node:module'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const require = createRequire(import.meta.url)
const { findAvailablePort } = require('../src/ports.js')

const desktopRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const electronBin = join(desktopRoot, 'node_modules', '.bin', process.platform === 'win32' ? 'electron.cmd' : 'electron')
const timeoutMs = 25_000

function createMockBackend() {
  const server = createServer((request, response) => {
    if (request.url === '/api/config') {
      response.writeHead(200, { 'content-type': 'application/json' })
      response.end(JSON.stringify({ ok: true, app_name: 'Voc Studio Launch Smoke' }))
      return
    }

    response.writeHead(200, { 'content-type': 'text/html; charset=utf-8' })
    response.end('<!doctype html><title>Voc Studio Launch Smoke</title><main>ready</main>')
  })

  return server
}

function listen(server, port) {
  return new Promise((resolve, reject) => {
    server.once('error', reject)
    server.listen(port, '127.0.0.1', resolve)
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

async function main() {
  const port = await findAvailablePort(44000, '127.0.0.1')
  const server = createMockBackend()
  await listen(server, port)

  const child = spawn(electronBin, ['.'], {
    cwd: desktopRoot,
    env: {
      ...process.env,
      VOC_STUDIO_BACKEND_URL: `http://127.0.0.1:${port}`,
      VOC_STUDIO_EXIT_AFTER_LOAD: '1',
    },
    stdio: ['ignore', 'pipe', 'pipe'],
  })

  let output = ''
  const timer = setTimeout(() => {
    output += `\nTimed out after ${timeoutMs}ms.`
    child.kill('SIGTERM')
  }, timeoutMs)

  child.stdout.on('data', (chunk) => {
    output += chunk.toString()
  })
  child.stderr.on('data', (chunk) => {
    output += chunk.toString()
  })

  const exitCode = await new Promise((resolve) => {
    child.on('error', (error) => {
      output += `\n${error.stack || error.message}`
      resolve(1)
    })
    child.on('exit', (code, signal) => {
      if (signal) {
        output += `\nElectron exited with signal ${signal}.`
      }
      resolve(code ?? (signal ? 1 : 0))
    })
  })

  clearTimeout(timer)
  await close(server)

  if (exitCode !== 0) {
    console.error('Electron launch smoke failed:')
    console.error(output.trim())
    process.exit(exitCode)
  }

  console.log(`Electron launch smoke passed on mock backend port ${port}.`)
}

main().catch((error) => {
  console.error('Electron launch smoke failed:')
  console.error(error.stack || error.message)
  process.exit(1)
})
