const path = require('node:path')
const { app, BrowserWindow, Menu, clipboard, ipcMain, shell } = require('electron')
const { getBackendState, openBackendInTerminal, startBackend, stopBackend } = require('./backend')
const { collectRuntimeChecks } = require('./diagnostics')
const { cacheDir, dataDir, logDir } = require('./paths')

let mainWindow = null

function shouldExitAfterFirstLoad() {
  return process.env.VOC_STUDIO_EXIT_AFTER_LOAD === '1'
}

function isAllowedAppUrl(targetUrl) {
  if (!targetUrl || targetUrl.startsWith('data:text/html')) return true
  let parsed
  let backend
  try {
    parsed = new URL(targetUrl)
    backend = new URL(getBackendState().url)
  } catch {
    return false
  }
  return parsed.origin === backend.origin
}

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

function createLoadingPage(message, detail = '') {
  const safeMessage = escapeHtml(message)
  const safeDetail = escapeHtml(detail)
  const hasDetail = Boolean(detail)
  const hasMultilineDetail = detail.includes('\n')

  return `data:text/html;charset=utf-8,${encodeURIComponent(`
    <!doctype html>
    <html lang="zh-CN">
      <head>
        <meta charset="utf-8" />
        <title>Voc Studio</title>
        <style>
          html, body { height: 100%; }
          body {
            margin: 0;
            display: grid;
            place-items: center;
            background: #f4f6f8;
            color: #18202f;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", sans-serif;
          }
          main {
            width: min(560px, calc(100vw - 48px));
            padding: 28px;
            border: 1px solid #dbe2ea;
            border-radius: 8px;
            background: white;
          }
          h1 { margin: 0 0 12px; font-size: 24px; }
          p { margin: 0; color: #64748b; line-height: 1.7; }
          pre {
            max-height: 220px;
            overflow: auto;
            margin-top: 16px;
            padding: 12px;
            border-radius: 6px;
            background: #0f172a;
            color: #d1fae5;
            white-space: pre-wrap;
          }
        </style>
      </head>
      <body>
        <main>
          <h1>${safeMessage}</h1>
          ${hasDetail && !hasMultilineDetail ? `<p>${safeDetail}</p>` : ''}
          ${hasMultilineDetail ? `<pre>${safeDetail}</pre>` : ''}
        </main>
      </body>
    </html>
  `)}`
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 960,
    minWidth: 1120,
    minHeight: 760,
    title: 'Voc Studio',
    backgroundColor: '#f4f6f8',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  })

  mainWindow.loadURL(createLoadingPage('正在启动 Voc Studio', '正在启动本地创作引擎，请稍候。'))
  attachWindowGuards(mainWindow)
  const win = mainWindow
  if (shouldExitAfterFirstLoad()) {
    win.webContents.once('did-finish-load', () => {
      setTimeout(() => app.quit(), 100)
    })
  }
  win.on('closed', () => {
    if (mainWindow === win) {
      mainWindow = null
    }
  })
  return mainWindow
}

function attachWindowGuards(win) {
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (url && !isAllowedAppUrl(url)) {
      shell.openExternal(url)
    }
    return { action: 'deny' }
  })

  win.webContents.on('will-navigate', (event, url) => {
    if (isAllowedAppUrl(url)) return
    event.preventDefault()
    shell.openExternal(url)
  })

  win.webContents.session.setPermissionRequestHandler((_webContents, _permission, callback) => {
    callback(false)
  })

  win.webContents.on('render-process-gone', (_event, details) => {
    const reason = details.reason ? `原因：${details.reason}` : ''
    win.loadURL(createLoadingPage('Voc Studio 窗口进程已退出', reason))
  })

  win.on('unresponsive', () => {
    win.loadURL(createLoadingPage('Voc Studio 暂无响应', '请等待当前任务结束，或从菜单重新载入窗口。'))
  })
}

function buildMenu() {
  const template = [
    {
      label: 'Voc Studio',
      submenu: [
        { role: 'about' },
        { type: 'separator' },
        {
          label: '打开数据目录',
          click: () => shell.openPath(dataDir()),
        },
        {
          label: '打开日志目录',
          click: () => shell.openPath(logDir()),
        },
        {
          label: '打开缓存目录',
          click: () => shell.openPath(cacheDir()),
        },
        {
          label: '打开本地服务',
          click: () => {
            const { url } = getBackendState()
            if (url) shell.openExternal(url)
          },
        },
        {
          label: '复制后端启动命令',
          click: () => clipboard.writeText(openBackendInTerminal()),
        },
        { type: 'separator' },
        { role: 'quit' },
      ],
    },
    {
      label: '视图',
      submenu: [
        { role: 'reload' },
        { role: 'forceReload' },
        { role: 'toggleDevTools' },
        { type: 'separator' },
        { role: 'resetZoom' },
        { role: 'zoomIn' },
        { role: 'zoomOut' },
        { type: 'separator' },
        { role: 'togglefullscreen' },
      ],
    },
  ]
  Menu.setApplicationMenu(Menu.buildFromTemplate(template))
}

function registerIpc() {
  ipcMain.handle('backend:status', () => getBackendState())
  ipcMain.handle('backend:copyLaunchCommand', () => {
    const command = openBackendInTerminal()
    clipboard.writeText(command)
    return command
  })
  ipcMain.handle('backend:openUrl', () => {
    const { url } = getBackendState()
    return url ? shell.openExternal(url) : ''
  })
  ipcMain.handle('shell:openDataDirectory', () => shell.openPath(dataDir()))
  ipcMain.handle('shell:openCacheDirectory', () => shell.openPath(cacheDir()))
  ipcMain.handle('shell:openLogDirectory', () => shell.openPath(logDir()))
  ipcMain.handle('desktop:copyDiagnostics', async () => {
    const diagnostics = await buildDiagnostics()
    const text = JSON.stringify(diagnostics, null, 2)
    clipboard.writeText(text)
    return text
  })
  ipcMain.handle('desktop:diagnostics', () => buildDiagnostics())
}

async function buildDiagnostics() {
  return {
    appName: app.getName(),
    appVersion: app.getVersion(),
    electronVersion: process.versions.electron,
    chromeVersion: process.versions.chrome,
    nodeVersion: process.versions.node,
    platform: process.platform,
    arch: process.arch,
    packaged: app.isPackaged,
    backend: getBackendState(),
    runtime: await collectRuntimeChecks(),
  }
}

app.setName('Voc Studio')
const hasSingleInstanceLock = app.requestSingleInstanceLock()

if (!hasSingleInstanceLock) {
  app.quit()
} else {
  registerIpc()

  app.on('second-instance', () => {
    if (!mainWindow) return
    if (mainWindow.isMinimized()) mainWindow.restore()
    mainWindow.show()
    mainWindow.focus()
  })

  app.whenReady().then(async () => {
    buildMenu()
    const win = createWindow()
    try {
      const backend = await startBackend()
      await win.loadURL(backend.url)
    } catch (error) {
      const logs = getBackendState().logs.slice(-80).join('\n')
      await win.loadURL(createLoadingPage('Voc Studio 启动失败', `${error.message}\n\n${logs}`))
    }

    app.on('activate', () => {
      if (BrowserWindow.getAllWindows().length === 0) {
        const reopened = createWindow()
        startBackend()
          .then((backend) => reopened.loadURL(backend.url))
          .catch((error) => {
            const logs = getBackendState().logs.slice(-80).join('\n')
            return reopened.loadURL(createLoadingPage('Voc Studio 启动失败', `${error.message}\n\n${logs}`))
          })
      }
    })
  })
}

app.on('before-quit', () => {
  stopBackend()
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit()
  }
})
