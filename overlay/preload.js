// preload.js
// The pill's only bridge to Electron: state, levels, config, offers and how
// close the cursor is in; its clickable area, settled state and clicks out. The page runs with context
// isolation and no Node access. The hub has its own bridge (hub-preload.js).

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('bridge', {
  onState: (fn) => ipcRenderer.on('state', (_e, msg) => fn(msg)),
  onLevel: (fn) => ipcRenderer.on('level', (_e, level) => fn(level)),
  onConfig: (fn) => ipcRenderer.on('config', (_e, config) => fn(config)),
  onHover: (fn) => ipcRenderer.on('hover', (_e, inside) => fn(inside)),
  onNear: (fn) => ipcRenderer.on('near', (_e, near) => fn(near)),
  onOffer: (fn) => ipcRenderer.on('offer', (_e, offer) => fn(offer)),
  onDragCancelled: (fn) => ipcRenderer.on('drag-cancelled', () => fn()),
  onDockLeave: (fn) => ipcRenderer.on('dock-leave', () => fn()),
  setHitRect: (rects) => ipcRenderer.send('pill:hit-rect', rects),
  setSettled: (settled) => ipcRenderer.send('pill:settled', settled),
  pillAction: (action) => ipcRenderer.send('pill:action', action),
  pillDrag: (grab) => ipcRenderer.send('pill:drag', grab),
  pillDragEnd: (center) => ipcRenderer.send('pill:drag-end', center),
  dockLeft: () => ipcRenderer.send('pill:dock-left'),
  pillDragCancel: () => ipcRenderer.send('pill:drag-cancel'),
  setReducedMotion: (reduce) => ipcRenderer.send('pill:motion', reduce),
});
