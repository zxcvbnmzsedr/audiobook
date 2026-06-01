const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('vocStudio', {
  backendStatus: () => ipcRenderer.invoke('backend:status'),
  copyBackendLaunchCommand: () => ipcRenderer.invoke('backend:copyLaunchCommand'),
  copyDiagnostics: () => ipcRenderer.invoke('desktop:copyDiagnostics'),
  diagnostics: () => ipcRenderer.invoke('desktop:diagnostics'),
  openBackendUrl: () => ipcRenderer.invoke('backend:openUrl'),
  openCacheDirectory: () => ipcRenderer.invoke('shell:openCacheDirectory'),
  openDataDirectory: () => ipcRenderer.invoke('shell:openDataDirectory'),
  openLogDirectory: () => ipcRenderer.invoke('shell:openLogDirectory'),
})
