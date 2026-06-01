const path = require('node:path')
const { app } = require('electron')

function projectRoot() {
  if (app.isPackaged) {
    return process.resourcesPath
  }
  return path.resolve(__dirname, '../..')
}

function backendDir() {
  if (app.isPackaged) {
    return path.join(process.resourcesPath, 'app')
  }
  return path.join(projectRoot(), 'app')
}

function backendLaunchRoot() {
  return app.isPackaged ? process.resourcesPath : projectRoot()
}

function dataDir() {
  if (process.env.VOC_STUDIO_DATA_DIR) {
    return path.resolve(process.env.VOC_STUDIO_DATA_DIR)
  }
  if (!app.isPackaged) {
    return projectRoot()
  }
  return path.join(app.getPath('userData'), 'data')
}

function cacheDir() {
  return path.join(app.getPath('userData'), 'cache')
}

function logDir() {
  return path.join(app.getPath('userData'), 'logs')
}

module.exports = {
  backendDir,
  backendLaunchRoot,
  cacheDir,
  dataDir,
  logDir,
  projectRoot,
}
