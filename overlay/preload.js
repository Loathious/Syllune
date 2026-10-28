// preload.js
// The pill's only bridge to Electron: state, levels and config in; its
// clickable area, settled state and clicks out. The page runs with context
// isolation and no Node access. The hub has its own bridge (hub-preload.js).

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('bridge', {
  onState: (fn) => ipcRenderer.on('state', (_e, msg) => fn(msg)),
  onLevel: (fn) => ipcRenderer.on('level', (_e, level) => fn(level)),
  onConfig: (fn) => ipcRenderer.on('config', (_e, config) => fn(config)),
  onHover: (fn) => ipcRenderer.on('hover', (_e, inside) => fn(inside)),
  onDragCancelled: (fn) => ipcRenderer.on('drag-cancelled', () => fn()),
  setHitRect: (rect) => ipcRenderer.send('pill:hit-rect', rect),
  setSettled: (settled) => ipcRenderer.send('pill:settled', settled),
  pillAction: (action) => ipcRenderer.send('pill:action', action),
  pillDrag: (delta) => ipcRenderer.send('pill:drag', delta),
  pillDragEnd: () => ipcRenderer.send('pill:drag-end'),
  pillDragCancel: () => ipcRenderer.send('pill:drag-cancel'),
  setReducedMotion: (reduce) => ipcRenderer.send('pill:motion', reduce),
});
