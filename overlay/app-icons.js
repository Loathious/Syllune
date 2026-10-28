// Resolve only executable leaf names from the local install, never renderer paths.
// Registry lookups read Windows App Paths; a missing icon uses the UI fallback.
// Resolution is cached so scrolling history never repeatedly queries the OS.
'use strict';
const fs = require('fs');
const path = require('path');
const {execFile} = require('child_process');
const {app} = require('electron');
const cache = new Map();

function registryPath(name, hive) {
  return new Promise(resolve => execFile('reg.exe', ['query', `${hive}\\Software\\Microsoft\\Windows\\CurrentVersion\\App Paths\\${name}`, '/ve'],
    {windowsHide: true, timeout: 1500, maxBuffer: 16384}, (error, stdout) => {
      const match = !error && /REG_SZ\s+(.+)/.exec(stdout);
      resolve(match ? match[1].trim().replace(/^"|"$/g, '') : null);
    }));
}

async function resolve(name) {
  if (typeof name !== 'string' || !/^[a-z0-9][a-z0-9 ._+-]{0,80}\.exe$/i.test(name)) return null;
  const key = name.toLowerCase();
  if (cache.has(key)) return cache.get(key);
  const work = (async () => {
    if (process.platform !== 'win32') return null;
    const candidates = [
      await registryPath(name, 'HKCU'), await registryPath(name, 'HKLM'),
      path.join(process.env.SystemRoot || 'C:\\Windows', 'System32', name),
    ];
    const known = {
      'chrome.exe': ['Google/Chrome/Application/chrome.exe'],
      'msedge.exe': ['Microsoft/Edge/Application/msedge.exe'],
      'firefox.exe': ['Mozilla Firefox/firefox.exe'],
      'code.exe': ['Microsoft VS Code/Code.exe', 'Programs/Microsoft VS Code/Code.exe'],
      'obsidian.exe': ['Programs/Obsidian/Obsidian.exe'],
      'spotify.exe': ['Spotify/Spotify.exe'],
    };
    for (const base of [process.env.ProgramFiles, process.env['ProgramFiles(x86)'], process.env.LOCALAPPDATA, process.env.APPDATA].filter(Boolean)) {
      for (const relative of known[key] || []) candidates.push(path.join(base, relative));
    }
    for (const candidate of candidates) {
      if (!candidate || !path.isAbsolute(candidate) || path.basename(candidate).toLowerCase() !== key) continue;
      try {
        if (fs.statSync(candidate).isFile()) {
          const icon = await app.getFileIcon(candidate, {size: 'normal'});
          if (!icon.isEmpty()) return icon.toDataURL();
        }
      } catch {}
    }
    return null;
  })();
  if (cache.size > 256) cache.clear();
  cache.set(key, work);
  return work;
}
module.exports = {resolve};
