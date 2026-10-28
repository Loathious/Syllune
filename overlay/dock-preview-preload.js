// This preview can receive only a highlight bit; it cannot act on the engine.
// Kept separate from the interactive pill bridge to retain click-through scope.
const {contextBridge, ipcRenderer} = require('electron');
contextBridge.exposeInMainWorld('dockPreview', {
  onHighlight: fn => ipcRenderer.on('dock:highlight', (_event, highlighted) => fn(!!highlighted)),
});
