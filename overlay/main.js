// main.js
// Electron side of the dictation UI. Started by the Python engine
// (electron_overlay.py), which streams JSON lines over a localhost socket
// (Electron's main process can't read a stdin pipe on Windows): state changes
// and voice levels at ~60 Hz; clicks on the pill go back the same way. Owns
// two windows: the pill, which never takes focus and only catches the mouse
// while the cursor is over the pill itself, and the hub (home, history,
// notes, styles, dictionary, settings), whose data calls are relayed to the
// engine as `rpc` messages.

const { app, BrowserWindow, ipcMain, screen, shell, clipboard } = require('electron');
const path = require('path');
const fs = require('fs');
const net = require('net');
const readline = require('readline');
const {spawn} = require('child_process');
const brand = require('./brand.json');
const updates = require('./release-updates');
const appIcons = require('./app-icons');
const docks = require('./dock-geometry');
const { pathToFileURL } = require('url');

// --- Startup ---------------------------------------------------------------
app.setName(brand.name);
app.setAppUserModelId(brand.appId);

// The pill animates continuously; never let Chromium throttle it.
app.commandLine.appendSwitch('disable-renderer-backgrounding');
app.commandLine.appendSwitch('disable-background-timer-throttling');

const argValue = (name) => {
  const i = process.argv.indexOf(name);
  return i >= 0 ? process.argv[i + 1] : null;
};
const notesDir = argValue('--notes-dir') || path.join(app.getPath('home'), 'Voice Notes');

let pill = null;
let hub = null;
let engine = null;               // socket to the Python engine
let position = 'bottom-center';
let idlePill = true;
let gameMode = false;
let meetingSince = null;
let pendingCount = 0;
let moveTimer = null;
let pillSettled = true;          // the renderer reports when nothing is on screen or moving
let quitting = false;
let reducedMotion = false;
let dragSession = null;
let previewWindows = [];
let previewDisplayId = null;

// A destroyed window object stays non-null until its 'closed' event, and any
// call on it throws; every timer and message path checks this first.
const alive = (win) => !!win && !win.isDestroyed();

if (!app.requestSingleInstanceLock()) app.quit();

// --- Errors ----------------------------------------------------------------
// The overlay runs unattended, so an uncaught error must never become Electron's
// modal error box, which a 20 Hz timer reopens the moment it is closed. Each
// distinct error is written once next to app.log instead.

const ERROR_LOG = path.join(app.getPath('appData'), 'whisperkey', 'overlay-errors.log');
const loggedErrors = new Set();
process.on('uncaughtException', (error) => {
  const text = String(error?.stack || error);
  if (loggedErrors.has(text) || loggedErrors.size >= 50) return;
  loggedErrors.add(text);
  try { fs.appendFileSync(ERROR_LOG, `${new Date().toISOString()} ${text}\n`); } catch {}
});

// Timers stop before exiting: app.exit destroys the windows first, and a tick
// landing in between must not touch them.
function quit(code = 0) {
  quitting = true;
  clearDockPreviews();
  stopHoverPoll();
  clearInterval(moveTimer);
  app.exit(code);
}

// Its own taskbar identity, so the hub shows the app's icon rather than
// grouping under Electron.
// An installed shortcut starts this shell, which hands lifetime to the engine.
// The engine reconnects using --port; it does not need a system Python/Node.
function launchBundledEngine() {
  const executable = path.join(process.resourcesPath, 'engine', 'SylluneEngine.exe');
  if (!fs.existsSync(executable)) throw new Error('The bundled Syllune engine is missing. Reinstall Syllune.');
  const child = spawn(executable, [], {detached: true, stdio: 'ignore', windowsHide: true, env: {
    ...process.env, SYLLUNE_ELECTRON_EXE: process.execPath, SYLLUNE_OVERLAY_DIR: __dirname,
  }});
  child.on('error', error => { require('electron').dialog.showErrorBox('Syllune could not start', error.message); app.exit(1); });
  child.once('spawn', () => { child.unref(); app.exit(0); });
}

app.whenReady().then(() => {
  if (app.isPackaged && !argValue('--port')) { launchBundledEngine(); return; }
  createPill();
  listenToEngine();
  const displaysChanged = () => { cancelPillDrag(); placePill(); };
  screen.on('display-metrics-changed', displaysChanged);
  screen.on('display-removed', displaysChanged);
  screen.on('display-added', displaysChanged);
});

// The engine owns our lifetime: no windows open must not quit the app.
app.on('window-all-closed', (e) => e.preventDefault());
app.on('before-quit', () => {
  quitting = true;
  clearDockPreviews();
  stopHoverPoll();
  clearInterval(moveTimer);
});

// --- Pill window -----------------------------------------------------------

function createPill() {
  pill = new BrowserWindow({
    width: docks.WINDOW_LENGTH,
    height: docks.WINDOW_CROSS,
    transparent: true,
    frame: false,
    resizable: false,
    movable: false,
    focusable: false,          // the paste must land in the user's window
    skipTaskbar: true,
    hasShadow: false,
    show: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      backgroundThrottling: false,
    },
  });
  pill.setAlwaysOnTop(true, 'screen-saver');
  pill.setIgnoreMouseEvents(true);
  pill.setVisibleOnAllWorkspaces(true);
  pill.loadFile(path.join(__dirname, 'pill.html'));
  // The engine's settings can arrive before the page has loaded; resend them.
  pill.webContents.on('did-finish-load', sendPillConfig);
  // Shown once and kept up: idle frames are transparent, so showing a
  // recording never pays a window-show delay or risks a focus change. Only
  // game mode hides the window for real.
  pill.once('ready-to-show', () => {
    placePill();
    if (!gameMode) pill.showInactive();
  });
  // A pill that closes while the engine still runs (a renderer crash, or
  // Windows closing it) comes back; a crashed page alone just reloads.
  pill.on('closed', () => {
    clearDockPreviews();
    dragSession = null;
    pill = null;
    hitRect = null;
    hovering = false;
    stopHoverPoll();
    clearInterval(moveTimer);
    if (!quitting) setTimeout(createPill, 500);
  });
  pill.webContents.on('render-process-gone', () => {
    cancelPillDrag();
    if (alive(pill)) pill.reload();
  });
}

function showPillWindow() {
  if (alive(pill) && !pill.isVisible()) pill.showInactive();
}

function sendToPill(channel, payload) {
  if (alive(pill)) pill.webContents.send(channel, payload);
}

// In game mode the window is hidden once the pill has finished animating out,
// so nothing transparent sits over the game.
function hidePillWindowIfIdle() {
  if (alive(pill) && gameMode && pillSettled && pill.isVisible()) {
    setPillHover(false);
    pill.hide();
  }
}

// Follows the monitor the cursor is on, so the pill appears where you look.
// Three spots: bottom center, or the middle of the left or right edge. On a
// side the window hugs that edge and pill.html draws the pill against it.
function placePill(smooth = false, display = null) {
  if (!alive(pill)) return;
  display ||= screen.getDisplayNearestPoint(screen.getCursorScreenPoint());
  const target = docks.pillBounds(position, display.workArea);
  clearInterval(moveTimer);
  const previous = pill.getBounds();
  // Resize before the settle: canvas orientation and native hit testing agree.
  if (previous.width !== target.width || previous.height !== target.height) hitRect = null;
  pill.setBounds({...previous, width: target.width, height: target.height});
  sendPillConfig();
  if (smooth && !reducedMotion && !gameMode && (previous.x !== target.x || previous.y !== target.y)) {
    const since = Date.now();
    moveTimer = setInterval(() => {
      if (!alive(pill)) { clearInterval(moveTimer); return; }
      const t = Math.min(1, (Date.now() - since) / 180), ease = 1 - (1 - t) ** 3;
      pill.setBounds({...target, x: Math.round(previous.x + (target.x - previous.x) * ease), y: Math.round(previous.y + (target.y - previous.y) * ease)});
      if (t === 1) clearInterval(moveTimer);
    }, 16);
  } else pill.setBounds(target);
  sendPillConfig();
}

function sendPillConfig() {
  sendToPill('config', { idlePill, gameMode, meetingSince, pendingCount, align: position.split('-')[1] });
}

// --- Pill hover ------------------------------------------------------------
// The window ignores the mouse except while the cursor is over the pill. That
// is decided by polling the cursor (20 Hz, only while the pill is clickable)
// rather than Electron's forwarded mouse moves, which install a system-wide
// low-level mouse hook that every mouse event would pass through.

const HOVER_POLL_MS = 50;
let hitRect = null;              // clickable area in window coordinates, from the renderer
let hovering = false;
let hoverTimer = null;

function setPillHover(inside) {
  if (inside === hovering || !alive(pill)) return;
  hovering = inside;
  pill.setIgnoreMouseEvents(!inside);
  pill.webContents.send('hover', inside);
}

function stopHoverPoll() {
  clearInterval(hoverTimer);
  hoverTimer = null;
}

function pollHover() {
  // Captured drags keep receiving pointer-up even after leaving the old shape.
  if (dragSession) { setPillHover(true); return; }
  if (!alive(pill) || !hitRect || gameMode || !pill.isVisible()) {
    setPillHover(false);
    return;
  }
  const cursor = screen.getCursorScreenPoint();
  const bounds = pill.getBounds();
  const x = cursor.x - bounds.x, y = cursor.y - bounds.y;
  setPillHover(x >= hitRect.x && x <= hitRect.x + hitRect.w && y >= hitRect.y && y <= hitRect.y + hitRect.h);
}

ipcMain.on('pill:hit-rect', (_e, rect) => {
  if (!alive(pill) || _e.sender !== pill.webContents) return;
  hitRect = rect;
  if (rect && !hoverTimer) hoverTimer = setInterval(pollHover, HOVER_POLL_MS);
  if (!rect && hoverTimer) {
    stopHoverPoll();
    setPillHover(false);
  }
});

ipcMain.on('pill:settled', (_e, settled) => {
  if (!alive(pill) || _e.sender !== pill.webContents) return;
  pillSettled = !!settled;
  hidePillWindowIfIdle();
});

// Clicks on the pill: dictate, stop, cancel.
ipcMain.on('pill:action', (_e, action) => {
  if (!alive(pill) || _e.sender !== pill.webContents) return;
  if (!['dictate', 'stop', 'cancel', 'meeting_stop', 'meeting_open', 'open_hub', 'pending_open'].includes(action)) return;
  engine?.write(JSON.stringify({ type: 'action', action }) + '\n');
});

// Only deliberate renderer drags create these three small, noninteractive
// windows. The monitor-sized desktop never becomes an invisible input layer.
function clearDockPreviews() {
  for (const preview of previewWindows) if (alive(preview)) preview.destroy();
  previewWindows = [];
  previewDisplayId = null;
}

function showDockPreviews(display, selected) {
  if (previewDisplayId !== display.id) {
    clearDockPreviews();
    previewDisplayId = display.id;
    previewWindows = docks.POSITIONS.map((dock) => {
      const rect = docks.dockBounds(dock, display.workArea);
      const preview = new BrowserWindow({
        ...rect, transparent: true, frame: false, focusable: false, skipTaskbar: true,
        resizable: false, movable: false, hasShadow: false, show: false,
        webPreferences: {preload: path.join(__dirname, 'dock-preview-preload.js'), sandbox: true},
      });
      preview.dockPosition = dock;
      preview.setIgnoreMouseEvents(true);
      preview.setAlwaysOnTop(true, 'screen-saver');
      preview.setVisibleOnAllWorkspaces(true);
      preview.loadFile(path.join(__dirname, 'dock-preview.html'), {query:{position:dock}});
      preview.once('ready-to-show', () => {
        if (!dragSession || !alive(preview)) return;
        preview.webContents.send('dock:highlight', dragSession.selected === dock);
        preview.showInactive();
      });
      return preview;
    });
  }
  for (const preview of previewWindows) if (alive(preview)) preview.webContents.send('dock:highlight', preview.dockPosition === selected);
}

function cancelPillDrag() {
  const session = dragSession;
  dragSession = null;
  clearDockPreviews();
  if (session && alive(pill)) {
    position = session.position;
    const display = screen.getDisplayMatching(session.bounds);
    placePill(false, display);
    sendToPill('drag-cancelled');
  }
}

ipcMain.on('pill:motion', (event, reduce) => {
  if (alive(pill) && event.sender === pill.webContents) reducedMotion = !!reduce;
});

// Screen deltas avoid feedback as the native window follows the pointer.
ipcMain.on('pill:drag', (event, delta) => {
  if (!alive(pill) || event.sender !== pill.webContents || gameMode) return;
  if (!delta || !Number.isFinite(delta.x) || !Number.isFinite(delta.y) || Math.abs(delta.x) > 1000 || Math.abs(delta.y) > 1000) return;
  clearInterval(moveTimer);
  const bounds = pill.getBounds();
  if (!dragSession) dragSession = {bounds, position};
  const point = screen.getCursorScreenPoint();
  const display = screen.getDisplayNearestPoint(point);
  dragSession.display = display;
  dragSession.selected = docks.candidate(point, display.workArea);
  setPillHover(true);
  pill.setBounds({...bounds, x: bounds.x + Math.round(delta.x), y: bounds.y + Math.round(delta.y)});
  showDockPreviews(display, dragSession.selected);
});
// Release commits the last highlighted offer, so pointer movement during an
// IPC round trip cannot silently choose a different dock from the preview.
ipcMain.on('pill:drag-end', (event) => {
  if (!alive(pill) || event.sender !== pill.webContents || !dragSession) return;
  const {selected:snapped, display} = dragSession;
  dragSession = null;
  clearDockPreviews();
  if (snapped !== position) {
    position = snapped;
    engine?.write(JSON.stringify({type: 'action', action: 'pill_position', position: snapped}) + '\n');
  }
  placePill(true, display);
});
ipcMain.on('pill:drag-cancel', event => {
  if (alive(pill) && event.sender === pill.webContents) cancelPillDrag();
});

// --- Engine messages -------------------------------------------------------

function listenToEngine() {
  const port = Number(argValue('--port'));
  if (!port) { quit(1); return; }
  const socket = net.connect({ host: '127.0.0.1', port });
  socket.setNoDelay(true);
  engine = socket;
  const rl = readline.createInterface({ input: socket });
  rl.on('line', (line) => {
    let msg;
    try { msg = JSON.parse(line); } catch { return; }
    handle(msg);
  });
  // Engine gone (closed or crashed): nothing left to show.
  socket.on('close', () => quit());
  socket.on('error', () => quit());
}

function handle(msg) {
  switch (msg.type) {
    case 'pending':
      pendingCount = Math.max(0, Math.min(9999, Number(msg.count) || 0));
      sendPillConfig();
      break;
    case 'meeting':
      meetingSince = Number.isFinite(msg.since) && msg.since > 0 ? msg.since : null;
      sendPillConfig();
      if (meetingSince && !gameMode) showPillWindow();
      break;
    case 'state':
      if (msg.state === 'recording') {
        placePill(true);
        showPillWindow();
      } else if (gameMode && msg.state !== 'hidden' && msg.state !== 'locked') {
        showPillWindow();         // a result to show for a dictation made in game mode
      }
      sendToPill('state', msg);
      break;
    case 'level':
      sendToPill('level', { v: msg.v, bands: msg.bands });
      break;
    case 'config':
      if (docks.POSITIONS.includes(msg.position) && msg.position !== position) {
        cancelPillDrag();
        position = msg.position;
        placePill();
      }
      if ('idle_pill' in msg) idlePill = !!msg.idle_pill;
      sendPillConfig();
      break;
    case 'game_mode':
      cancelPillDrag();
      gameMode = !!msg.on;
      sendPillConfig();
      if (gameMode) hidePillWindowIfIdle();
      else showPillWindow();
      break;
    case 'note_saved':
      if (gameMode) showPillWindow();
      sendToPill('state', { state: 'note_saved' });
      if (alive(hub)) hub.webContents.send('hub:event', 'notes');
      break;
    case 'open_hub':
      openHub(msg.page);
      break;
    case 'hub_event':
      if (alive(hub)) hub.webContents.send('hub:event', String(msg.name || ''));
      break;
    case 'rpc':
      settleCall(msg);
      break;
    case 'quit':
      quit();
      break;
  }
}

// --- Hub window ------------------------------------------------------------
// One window for everything that isn't the pill. Opening it again focuses it
// and switches page instead of stacking windows.

const HUB_PAGES = ['home', 'history', 'notes', 'meetings', 'styles', 'dictionary', 'settings', 'onboarding'];
const HUB_BG = '#101929';
const APP_ICON = path.join(__dirname, 'assets', 'syllune.ico');

function openHub(page) {
  page = HUB_PAGES.includes(page) ? page : 'home';
  if (alive(hub)) {
    if (hub.isMinimized()) hub.restore();
    hub.show();
    hub.focus();
    hub.webContents.send('hub:navigate', page);
    return;
  }
  hub = new BrowserWindow({
    width: 1040,
    height: 700,
    minWidth: 760,
    minHeight: 520,
    title: brand.name,
    icon: APP_ICON,
    backgroundColor: HUB_BG,     // no white flash before the page paints
    titleBarStyle: 'hidden',
    titleBarOverlay: { color: HUB_BG, symbolColor: '#8a8f9b', height: 36 },
    show: false,
    webPreferences: {
      preload: path.join(__dirname, 'hub-preload.js'),
      sandbox: true,
      spellcheck: false,
    },
  });
  // The hub only ever shows its own pages: no navigation, no popups.
  hub.webContents.on('will-navigate', (e) => e.preventDefault());
  hub.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  hub.loadFile(path.join(__dirname, 'hub', 'index.html'), { query: { page } });
  hub.once('ready-to-show', () => hub.show());
  hub.on('closed', () => { hub = null; });
}

// Requests from other windows (the pill) never reach the engine or the files.
const fromHub = (e) => alive(hub) && e.sender === hub.webContents;

// --- Hub calls to the engine -----------------------------------------------
// The page awaits `hub:call`; it becomes an `rpc` line to the engine, and the
// engine's reply (matched by id) settles it. Replies are {ok, result | error}
// rather than rejections so the page gets the engine's message unwrapped.

const CALL_TIMEOUT_MS = 20000;
const pendingCalls = new Map();
let lastCallId = 0;

ipcMain.handle('hub:call', (e, method, params) => {
  if (!fromHub(e)) return { ok: false, error: 'not allowed' };
  return callEngine(method, params);
});

function callEngine(method, params) {
  if (!engine || engine.destroyed) return { ok: false, error: "Whisper Local isn't running" };
  const id = ++lastCallId;
  return new Promise((resolve) => {
    const timer = setTimeout(() => {
      pendingCalls.delete(id);
      resolve({ ok: false, error: 'Whisper Local took too long to answer' });
    }, CALL_TIMEOUT_MS);
    pendingCalls.set(id, (reply) => {
      clearTimeout(timer);
      resolve(reply);
    });
    engine.write(JSON.stringify({ type: 'rpc', id, method: String(method), params: params || {} }) + '\n');
  });
}

// The engine resolves opaque IDs. A second real-path check in Electron limits
// playback to this app's WAV archives, including protection against symlinks.
ipcMain.handle('hub:audio-source', async (event, kind, id, track) => {
  if (!fromHub(event)) throw new Error('Not allowed');
  const reply = await callEngine('audio_source', {kind, entry_id: id, track});
  if (!reply.ok) throw new Error(reply.error);
  const full = fs.realpathSync(reply.result.path);
  const dataRoot = path.join(app.getPath('appData'), 'whisperkey');
  const folder = fs.realpathSync(path.join(dataRoot, kind === 'dictation' ? 'dictation_audio' : 'meetings'));
  const relative = path.relative(folder, full);
  if (!relative || relative.startsWith('..') || path.isAbsolute(relative) || path.extname(full).toLowerCase() !== '.wav' || !fs.statSync(full).isFile()) throw new Error('Invalid recording');
  return pathToFileURL(full).href;
});

function settleCall(msg) {
  const settle = pendingCalls.get(msg.id);
  if (!settle) return;
  pendingCalls.delete(msg.id);
  settle(msg.ok ? { ok: true, result: msg.result } : { ok: false, error: String(msg.error || 'Something went wrong') });
}

ipcMain.handle('hub:copy', (e, text) => {
  if (fromHub(e)) clipboard.writeText(String(text));
});

ipcMain.handle('hub:app-icon', (event, name) => fromHub(event) ? appIcons.resolve(name) : null);
ipcMain.handle('hub:update-check', event => {
  if (!fromHub(event)) throw new Error('Not allowed');
  return updates.check();
});
ipcMain.handle('hub:open-releases', event => {
  if (!fromHub(event)) throw new Error('Not allowed');
  const url = updates.releaseURL();
  if (!url) throw new Error('No public release repository is configured yet.');
  return shell.openExternal(url);
});

// --- Notes files -----------------------------------------------------------
// The hub's Notes page reads and writes the markdown files directly; the
// engine only creates them.

// Only plain .md files directly inside the notes folder; names come from the
// renderer, so anything with a path separator is refused.
function notePath(name) {
  if (typeof name !== 'string' || name !== path.basename(name) || !name.endsWith('.md')) {
    throw new Error('bad note name');
  }
  return path.join(notesDir, name);
}

ipcMain.handle('notes:list', (e) => {
  if (!fromHub(e)) return [];
  fs.mkdirSync(notesDir, { recursive: true });
  return fs.readdirSync(notesDir)
    .filter((f) => f.endsWith('.md'))
    .map((name) => {
      const full = path.join(notesDir, name);
      const text = fs.readFileSync(full, 'utf8');
      return { name, text, mtime: fs.statSync(full).mtimeMs };
    })
    .sort((a, b) => b.name.localeCompare(a.name));
});
ipcMain.handle('notes:save', (e, name, text) => {
  if (fromHub(e)) fs.writeFileSync(notePath(name), String(text), 'utf8');
});
// Recycle Bin, not a hard delete: a misclick is recoverable.
ipcMain.handle('notes:trash', (e, name) => {
  if (fromHub(e)) return shell.trashItem(notePath(name));
});
ipcMain.handle('notes:reveal', (e) => {
  if (fromHub(e)) return shell.openPath(notesDir);
});
