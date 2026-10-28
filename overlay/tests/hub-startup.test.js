// Exercise the real preload bridge before the hub finishes its initial RPC.
// Engine navigation must survive this interval rather than silently vanishing.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {EventEmitter} = require('node:events');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

test('hub navigation received before setup status completes is delivered when the page subscribes', () => {
  const ipc = new EventEmitter();
  let bridge;
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../hub-preload.js'), 'utf8'), {
    require(name) {
      assert.equal(name, 'electron');
      return {ipcRenderer: ipc, contextBridge: {exposeInMainWorld(name, value) { bridge = value; }}};
    },
  });
  ipc.emit('hub:navigate', {}, 'onboarding');
  const pages = [];
  bridge.onNavigate(page => pages.push(page));
  assert.deepEqual(pages, ['onboarding']);
  ipc.emit('hub:navigate', {}, 'settings');
  assert.deepEqual(pages, ['onboarding', 'settings']);
});
