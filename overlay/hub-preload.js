// hub-preload.js
// The hub window's bridge: `call` reaches the engine (hub_api.py), `notes`
// reads and writes the notes folder, and the engine can switch page or say
// that something changed. Runs sandboxed; the page gets nothing else.

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('hub', {
  // Resolves with the result, or rejects with the engine's own message.
  call: (method, params) => ipcRenderer.invoke('hub:call', method, params || {}).then((reply) => {
    if (reply && reply.ok) return reply.result;
    throw new Error((reply && reply.error) || 'Something went wrong');
  }),
  onNavigate: (fn) => ipcRenderer.on('hub:navigate', (_e, page) => fn(page)),
  onEvent: (fn) => ipcRenderer.on('hub:event', (_e, name) => fn(name)),
  copy: (text) => ipcRenderer.invoke('hub:copy', text),
  appIcon: (name) => ipcRenderer.invoke('hub:app-icon', name),
  updateCheck: () => ipcRenderer.invoke('hub:update-check'),
  openReleases: () => ipcRenderer.invoke('hub:open-releases'),
  audioSource: (kind, id, track) => ipcRenderer.invoke('hub:audio-source', kind, id, track),
  notes: {
    list: () => ipcRenderer.invoke('notes:list'),
    save: (name, text) => ipcRenderer.invoke('notes:save', name, text),
    trash: (name) => ipcRenderer.invoke('notes:trash', name),
    reveal: () => ipcRenderer.invoke('notes:reveal'),
  },
});
