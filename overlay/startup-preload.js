// Minimal bridge for the startup/recovery window. Only its own buttons may
// retry the bundled engine, quit the app or open the fixed diagnostics folder.
const {contextBridge, ipcRenderer} = require('electron');
contextBridge.exposeInMainWorld('startup', {
  retry: () => ipcRenderer.send('startup:retry'),
  logs: () => ipcRenderer.send('startup:logs'),
  quit: () => ipcRenderer.send('startup:quit'),
  onStatus: callback => ipcRenderer.on('startup:status', (_event, status) => callback(status)),
});
