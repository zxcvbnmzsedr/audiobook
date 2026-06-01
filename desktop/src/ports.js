const net = require('node:net')

function isPortAvailable(port, host = '127.0.0.1') {
  return new Promise((resolve) => {
    const server = net.createServer()
    server.once('error', () => resolve(false))
    server.once('listening', () => {
      server.close(() => resolve(true))
    })
    server.listen(port, host)
  })
}

async function findAvailablePort(startPort = 4200, host = '127.0.0.1') {
  for (let port = startPort; port < startPort + 100; port += 1) {
    if (await isPortAvailable(port, host)) {
      return port
    }
  }
  throw new Error(`No available port found from ${startPort} to ${startPort + 99}.`)
}

module.exports = {
  findAvailablePort,
}
