const { spawn } = require('node:child_process')

const COMMANDS = [
  { name: 'uv', args: ['--version'], required: true, purpose: 'Python dependency runner' },
  { name: process.platform === 'win32' ? 'python' : 'python3', args: ['--version'], required: true, purpose: 'Backend runtime' },
  { name: 'ffmpeg', args: ['-version'], required: false, purpose: 'Audio export and muxing' },
]

function runCommand(command, args) {
  return new Promise((resolve) => {
    const child = spawn(command, args, { shell: false })
    let output = ''

    child.stdout.on('data', (data) => {
      output += data.toString()
    })
    child.stderr.on('data', (data) => {
      output += data.toString()
    })
    child.on('error', (error) => {
      resolve({ available: false, error: error.message })
    })
    child.on('close', (code) => {
      const firstLine = output.split(/\r?\n/).find((line) => line.trim())?.trim() ?? ''
      resolve({
        available: code === 0,
        code,
        version: firstLine,
      })
    })
  })
}

async function collectRuntimeChecks() {
  const checks = await Promise.all(COMMANDS.map(async (definition) => {
    const result = await runCommand(definition.name, definition.args)
    return {
      ...definition,
      ...result,
    }
  }))

  return {
    checkedAt: new Date().toISOString(),
    commands: checks,
  }
}

module.exports = {
  collectRuntimeChecks,
}
