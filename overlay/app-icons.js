// app-icons.js
// Finds the icon of an app the user dictated into, from its executable leaf
// name only (never a renderer path). Looks in App Paths, Store app folders,
// Start menu shortcuts and Windows folders, and keeps every icon it found so
// History and Styles still show it after the app is closed or updated.
'use strict';
const fs = require('fs');
const path = require('path');
const {execFile} = require('child_process');
const {app, nativeImage, shell} = require('electron');

const NAME_PATTERN = /^[a-z0-9][a-z0-9 ._+-]{0,80}\.exe$/i;
const INDEX_TTL_MS = 5 * 60 * 1000;     // installed apps change rarely
const SAVED_LIMIT = 200;

const lookups = new Map();               // name -> Promise<dataURL | null>
let saved = null;                        // name -> dataURL, kept on disk
let saveTimer = null;

// =========================================================================
// Remembered icons
// =========================================================================

const savedFile = () => path.join(app.getPath('userData'), 'app-icons.json');

function savedIcons() {
  if (saved) return saved;
  try {
    const data = JSON.parse(fs.readFileSync(savedFile(), 'utf8'));
    saved = new Map(Object.entries(data).filter(([name, url]) => NAME_PATTERN.test(name) && /^data:image\/png;base64,/.test(url)));
  } catch { saved = new Map(); }
  return saved;
}

// Written shortly after the last find, so a History page full of new apps
// costs one write.
function remember(name, url) {
  const icons = savedIcons();
  icons.delete(name);
  icons.set(name, url);
  while (icons.size > SAVED_LIMIT) icons.delete(icons.keys().next().value);
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => {
    try { fs.writeFileSync(savedFile(), JSON.stringify(Object.fromEntries(icons))); } catch {}
  }, 1000);
}

// =========================================================================
// Where installed apps live
// =========================================================================

function regQuery(args) {
  return new Promise(resolve => execFile('reg.exe', ['query', ...args],
    {windowsHide: true, timeout: 4000, maxBuffer: 4 * 1024 * 1024}, (error, stdout) => resolve(error ? '' : stdout)));
}

async function appPath(name, hive) {
  const output = await regQuery([`${hive}\\Software\\Microsoft\\Windows\\CurrentVersion\\App Paths\\${name}`, '/ve']);
  const match = /REG_SZ\s+(.+)/.exec(output);
  return match ? match[1].trim().replace(/^"|"$/g, '') : null;
}

// A cached, lazily rebuilt index: built on the first miss, then reused for
// a few minutes so a page of unknown apps triggers one scan, not one each.
function cachedIndex(build) {
  let work = null, builtAt = 0;
  return () => {
    if (!work || Date.now() - builtAt > INDEX_TTL_MS) { builtAt = Date.now(); work = build().catch(() => []); }
    return work;
  };
}

// Store (MSIX) apps such as Claude and ChatGPT live in per-version folders
// under WindowsApps; the per-user package registry names each one's root.
const storeRoots = cachedIndex(async () => {
  const output = await regQuery(['HKCU\\Software\\Classes\\Local Settings\\Software\\Microsoft\\Windows\\CurrentVersion\\AppModel\\Repository\\Packages',
    '/s', '/v', 'PackageRootFolder']);
  return [...output.matchAll(/PackageRootFolder\s+REG_SZ\s+(.+)/g)].map(match => match[1].trim())
    .filter(root => path.isAbsolute(root) && !/\\(Microsoft\.(NET|VCLibs|UI\.Xaml|WindowsAppRuntime)|MicrosoftWindows\.)/i.test(root));
});

// Start menu shortcuts cover classic installs in versioned folders (Discord,
// Slack): their shortcut starts Update.exe with "--processStart Name.exe".
const shortcutTargets = cachedIndex(async () => {
  const targets = new Map();
  const roots = [process.env.ProgramData && path.join(process.env.ProgramData, 'Microsoft', 'Windows', 'Start Menu', 'Programs'),
    process.env.APPDATA && path.join(process.env.APPDATA, 'Microsoft', 'Windows', 'Start Menu', 'Programs')].filter(Boolean);
  const walk = (folder, depth) => {
    let entries = [];
    try { entries = fs.readdirSync(folder, {withFileTypes: true}); } catch { return; }
    for (const entry of entries) {
      const full = path.join(folder, entry.name);
      if (entry.isDirectory() && depth < 3) walk(full, depth + 1);
      else if (entry.isFile() && /\.lnk$/i.test(entry.name)) {
        try {
          const link = shell.readShortcutLink(full);
          if (!link.target || !/\.exe$/i.test(link.target)) continue;
          const started = /--processStart(?:AndWait)?\s+"?([^"\s]+\.exe)/i.exec(link.args || '');
          const name = (started ? started[1] : path.basename(link.target)).toLowerCase();
          if (!targets.has(name)) targets.set(name, link.target);
        } catch {}
      }
    }
  };
  for (const root of roots) walk(root, 0);
  return targets;
});

// Familiar per-user and per-machine installs the other sources can miss.
const KNOWN = {
  'chrome.exe': ['Google/Chrome/Application/chrome.exe'],
  'msedge.exe': ['Microsoft/Edge/Application/msedge.exe'],
  'firefox.exe': ['Mozilla Firefox/firefox.exe'],
  'code.exe': ['Microsoft VS Code/Code.exe', 'Programs/Microsoft VS Code/Code.exe'],
  'cursor.exe': ['Programs/cursor/Cursor.exe'],
  'obsidian.exe': ['Programs/Obsidian/Obsidian.exe'],
  'spotify.exe': ['Spotify/Spotify.exe'],
  'notion.exe': ['Programs/Notion/Notion.exe'],
};

// Every place the executable may be, cheapest first.
async function* candidates(name) {
  yield await appPath(name, 'HKCU');
  yield await appPath(name, 'HKLM');
  const windows = process.env.SystemRoot || 'C:\\Windows';
  yield path.join(windows, 'System32', name);
  yield path.join(windows, name);
  for (const base of [process.env.ProgramFiles, process.env['ProgramFiles(x86)'], process.env.LOCALAPPDATA, process.env.APPDATA].filter(Boolean)) {
    for (const relative of KNOWN[name] || []) yield path.join(base, relative);
  }
  yield (await shortcutTargets()).get(name);
  for (const root of await storeRoots()) {
    yield path.join(root, name);
    yield path.join(root, 'app', name);
  }
}

// =========================================================================
// Lookup
// =========================================================================

// Store apps declare their tile logo in the package manifest; many of their
// executables carry no icon at all. The "unplated" variant suits the dark hub.
const LOGO_VARIANTS = ['targetsize-64_altform-unplated', 'targetsize-48_altform-unplated', 'targetsize-32_altform-unplated', 'scale-200', 'scale-100'];

function storeLogo(root, name) {
  const manifest = fs.readFileSync(path.join(root, 'AppxManifest.xml'), 'utf8');
  for (const match of manifest.matchAll(/<Application\b[^>]*?Executable="([^"]+)"[\s\S]*?Square44x44Logo="([^"]+)"/g)) {
    if (path.basename(match[1]).toLowerCase() !== name) continue;
    const logo = path.join(root, match[2]);
    const stem = logo.replace(/\.png$/i, '');
    for (const file of [...LOGO_VARIANTS.map(variant => `${stem}.${variant}.png`), logo]) {
      const image = fs.existsSync(file) ? nativeImage.createFromPath(file) : null;
      if (image && !image.isEmpty()) return image.resize({width: 64, height: 64, quality: 'best'}).toDataURL();
    }
  }
  return null;
}

async function find(name) {
  const roots = await storeRoots();
  for await (const candidate of candidates(name)) {
    if (!candidate || !path.isAbsolute(candidate)) continue;
    if (!fs.statSync(candidate, {throwIfNoEntry: false})?.isFile()) continue;
    // A package folder without a readable manifest still has the file's own icon.
    const root = roots.find(folder => candidate.toLowerCase().startsWith(folder.toLowerCase() + path.sep));
    let logo = null;
    if (root) { try { logo = storeLogo(root, name); } catch {} }
    if (logo) return logo;
    // A shortcut may start a launcher (Update.exe) whose icon is the app's.
    try {
      const icon = await app.getFileIcon(candidate, {size: 'normal'});
      if (!icon.isEmpty()) return icon.toDataURL();
    } catch {}
  }
  return null;
}

// A fresh find wins over a remembered icon (the app may have changed its
// look); the remembered one covers apps that were uninstalled or moved.
function resolve(name) {
  if (typeof name !== 'string' || !NAME_PATTERN.test(name) || process.platform !== 'win32') return Promise.resolve(null);
  const key = name.toLowerCase();
  if (!lookups.has(key)) {
    if (lookups.size > 256) lookups.clear();
    lookups.set(key, find(key).then(url => {
      if (url) { remember(key, url); return url; }
      return savedIcons().get(key) || null;
    }));
  }
  return lookups.get(key);
}

module.exports = {resolve};
