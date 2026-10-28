// tests/pill-electron-check.js
// Renders pill.html in real Electron with a stub bridge (no engine, microphone
// or app restart), walks every state on every dock, checks hit areas, clicks,
// drags and dock switching, and writes a contact sheet for a visual review.
// Run: node_modules/.bin/electron tests/pill-electron-check.js
const {app, BrowserWindow} = require('electron');
const fs = require('fs');
const path = require('path');
const docks = require('../dock-geometry');

const OUT = path.join(__dirname, '..', '..', '.temp', 'syllune-pill-states.png');
const AREA = {x: 0, y: 0, width: 1920, height: 1040};
const STATES = ['idle', 'hover', 'pending', 'meeting', 'listen', 'locked', 'processing', 'success', 'saved', 'error', 'notice'];
const SETTLE_MS = 650;
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));

// The stub bridge records what the page sends and lets the check push events.
const STUB = `
  window.pillEvents = {}; window.pillCalls = []; window.drawnText = []; window.hitRects = null;
  const fillText = CanvasRenderingContext2D.prototype.fillText;
  CanvasRenderingContext2D.prototype.fillText = function(...args) { drawnText.push(args[0]); return fillText.apply(this, args); };
  window.bridge = {
    onState: fn => pillEvents.state = fn, onConfig: fn => pillEvents.config = fn, onLevel: fn => pillEvents.level = fn,
    onHover: fn => pillEvents.hover = fn, onDragCancelled: fn => pillEvents.cancelled = fn, onDockLeave: fn => pillEvents.dockLeave = fn,
    setHitRect: rects => window.hitRects = rects, setSettled: () => {}, setReducedMotion: () => {},
    pillAction: a => pillCalls.push(['action', a]), pillDrag: d => pillCalls.push(['drag', d]),
    pillDragEnd: c => pillCalls.push(['end', c]), pillDragCancel: () => pillCalls.push(['cancel']),
    dockLeft: () => pillCalls.push(['left']),
  };`;

async function run() {
  const failures = [], captures = [];
  const preload = path.join(app.getPath('temp'), 'syllune-pill-stub.js');
  fs.writeFileSync(preload, STUB);
  const win = new BrowserWindow({
    width: docks.WINDOW_WIDTH, height: docks.WINDOW_HEIGHT, show: false, transparent: true, frame: false,
    webPreferences: {preload, contextIsolation: false, sandbox: false, offscreen: true, backgroundThrottling: false},
  });
  win.webContents.on('console-message', (event) => { if (event.level === 'error') failures.push(`console: ${event.message}`); });
  await win.loadFile(path.join(__dirname, '..', 'pill.html'));
  const js = (code) => win.webContents.executeJavaScript(code);
  const click = async (x, y) => {
    win.webContents.sendInputEvent({type: 'mouseMove', x, y});
    win.webContents.sendInputEvent({type: 'mouseDown', x, y, button: 'left', clickCount: 1});
    win.webContents.sendInputEvent({type: 'mouseUp', x, y, button: 'left', clickCount: 1});
    await wait(60);
  };

  for (const dock of docks.POSITIONS) {
    // Where the recording capsule must sit inside the window on this dock.
    const windowBounds = docks.pillBounds(dock, AREA), slot = docks.dockBounds(dock, AREA);
    const expected = {x: slot.x - windowBounds.x, y: slot.y - windowBounds.y, w: slot.width, h: slot.height};
    for (const state of STATES) {
      await js(`(() => {
        const state = ${JSON.stringify(state)};
        drawnText.length = 0;
        pillEvents.config({dock: ${JSON.stringify(dock)}, idlePill: true, meetingSince: state === 'meeting' ? Date.now() - 15000 : null, pendingCount: state === 'pending' ? 2 : 0});
        pillEvents.state({state: 'hidden'}); pillEvents.hover(['hover', 'pending', 'meeting'].includes(state));
        if (['listen', 'locked', 'notice'].includes(state)) { pillEvents.state({state: 'recording'}); pillEvents.level({v: .035, bands: [-20, -23, -27, -25, -35, -39]}); }
        if (state === 'locked') pillEvents.state({state: 'locked', value: true});
        if (['processing', 'success'].includes(state)) pillEvents.state({state});
        if (state === 'saved') pillEvents.state({state: 'note_saved'});
        if (state === 'error') pillEvents.state({state: 'error', message: 'Retry dictation'});
        if (state === 'notice') pillEvents.state({state: 'notice', message: 'Microphone changed'});
      })()`);
      await wait(SETTLE_MS);
      const result = await js(`({rects: hitRects, status: document.getElementById('status').textContent})`);
      const image = (await win.webContents.capturePage()).toDataURL();
      if (!result.status) failures.push(`${dock}/${state}: no accessible status`);
      for (const r of result.rects || []) {
        if (r.x < 0 || r.y < 0 || r.x + r.w > docks.WINDOW_WIDTH || r.y + r.h > docks.WINDOW_HEIGHT) failures.push(`${dock}/${state}: hit area leaves the window ${JSON.stringify(r)}`);
      }
      if (state === 'listen') {
        const pill = result.rects && result.rects[0];
        const drawn = pill && {x: pill.x + 6, y: pill.y + 6, w: pill.w - 12, h: pill.h - 12};
        if (!drawn || Object.keys(expected).some(k => Math.abs(drawn[k] - expected[k]) > 1)) failures.push(`${dock}/listen: capsule ${JSON.stringify(drawn)} is not on its preview ${JSON.stringify(expected)}`);
      }
      captures.push({dock, state, image});
    }
  }

  // Locked recording on the left dock: the stop button clicks, it never drags.
  const left = docks.dockBounds('middle-left', AREA), leftWindow = docks.pillBounds('middle-left', AREA);
  await js(`pillEvents.config({dock: 'middle-left', meetingSince: null, pendingCount: 0}); pillEvents.state({state: 'hidden'});
    pillEvents.state({state: 'recording'}); pillEvents.state({state: 'locked', value: true}); pillEvents.hover(true); pillCalls.length = 0;`);
  await wait(SETTLE_MS);
  const stopX = left.x - leftWindow.x + 128 - 18, rowY = left.y - leftWindow.y + left.height / 2;
  await click(stopX, rowY);
  let calls = await js('pillCalls.slice()');
  if (!calls.some(c => c[0] === 'action' && c[1] === 'stop') || calls.some(c => c[0] === 'drag')) failures.push(`stop click: ${JSON.stringify(calls)}`);

  // Resting handle: a click anywhere in its catch area starts a dictation.
  await js(`pillEvents.state({state: 'hidden'}); pillEvents.hover(true); pillCalls.length = 0;`);
  await wait(SETTLE_MS);
  await click(20, rowY + 30);
  calls = await js('pillCalls.slice()');
  if (!calls.some(c => c[0] === 'action' && c[1] === 'dictate')) failures.push(`catch-area click: ${JSON.stringify(calls)}`);

  // A small wobble is a click, a real move is a drag, and release reports where the pill is.
  await js(`pillEvents.state({state: 'hidden'}); pillEvents.hover(true); pillCalls.length = 0;`);
  await wait(SETTLE_MS);
  // Offscreen windows have no screen, so the screen point is given explicitly.
  const pointer = (type, x, y, extra = {}) => win.webContents.sendInputEvent({type, x, y, globalX: x + 500, globalY: y + 500, ...extra});
  const held = {modifiers: ['leftButtonDown']}, button = {button: 'left', clickCount: 1};
  pointer('mouseMove', 28, rowY);
  pointer('mouseDown', 28, rowY, button);
  pointer('mouseMove', 30, rowY + 2, held);
  await wait(60);
  if ((await js('pillCalls.slice()')).length) failures.push('subthreshold motion acted');
  pointer('mouseMove', 60, rowY - 10, held);
  pointer('mouseMove', 90, rowY - 20, held);
  pointer('mouseUp', 90, rowY - 20, button);
  await wait(60);
  calls = await js('pillCalls.slice()');
  const end = calls.find(c => c[0] === 'end');
  if (!calls.some(c => c[0] === 'drag') || !end || !Number.isFinite(end[1]?.x) || calls.some(c => c[0] === 'action')) failures.push(`drag: ${JSON.stringify(calls)}`);

  // Changing docks: the pill reports itself gone, then arrives on the new edge.
  await js(`pillCalls.length = 0; pillEvents.hover(false); pillEvents.dockLeave();`);
  await wait(400);
  calls = await js('pillCalls.slice()');
  if (!calls.some(c => c[0] === 'left')) failures.push('dock leave was never reported');
  await js(`pillEvents.config({dock: 'middle-right', arrive: true}); pillEvents.state({state: 'recording'});`);
  await wait(SETTLE_MS);
  const arrived = await js('hitRects');
  const right = docks.dockBounds('middle-right', AREA), rightWindow = docks.pillBounds('middle-right', AREA);
  if (!arrived || Math.abs(arrived[0].x + 6 - (right.x - rightWindow.x)) > 1) failures.push(`arrival rect ${JSON.stringify(arrived)}`);

  // Contact sheet from the actual transparent captures, on a light and a dark desktop.
  const sheet = new BrowserWindow({width: 1600, height: 900, show: false, webPreferences: {offscreen: true}});
  const cells = captures.map(c => `<figure><figcaption>${c.dock} / ${c.state}</figcaption><img src="${c.image}"></figure>`).join('');
  await sheet.loadURL('data:text/html;charset=utf-8,' + encodeURIComponent(`<html><body style="margin:0;font:12px Segoe UI;color:#17243b">
    <style>main{display:grid;grid-template-columns:repeat(6,1fr);gap:6px;padding:10px}figure{margin:0}img{width:100%;display:block;border-radius:4px}
    .light img{background:#e8edf3}.dark{background:#11161f;color:#c9d3df}.dark img{background:#2a3240}</style>
    <main class="light">${cells}</main><main class="dark">${cells}</main></body></html>`));
  const height = await sheet.webContents.executeJavaScript('document.body.scrollHeight');
  sheet.setContentSize(1600, height);
  await wait(300);
  fs.mkdirSync(path.dirname(OUT), {recursive: true});
  fs.writeFileSync(OUT, (await sheet.webContents.capturePage()).toPNG());

  console.log(failures.length ? 'FAIL\n' + failures.join('\n') : `PASS ${captures.length} states, contact sheet ${OUT}`);
  app.exit(failures.length ? 1 : 0);
}

app.disableHardwareAcceleration();
app.whenReady().then(run).catch(error => { console.error(error); app.exit(1); });
