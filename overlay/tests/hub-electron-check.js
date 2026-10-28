// tests/hub-electron-check.js
// Renders the hub in real Electron with made-up engine replies (no engine,
// microphone or downloads): walks the setup guide for a new and an existing
// install, opens Styles to load real app icons, and saves one screenshot
// per view for a visual review.
// Run: node_modules/electron/dist/electron.exe tests/hub-electron-check.js
const {app, BrowserWindow, ipcMain} = require('electron');
const fs = require('fs');
const path = require('path');
const appIcons = require('../app-icons');

// Each renderer check owns its browser cache; simultaneous checks must not
// contend with one another or with a real development Electron session.
app.setPath('userData', path.join(app.getPath('temp'), `syllune-hub-check-${process.pid}`));

const OUT = path.join(__dirname, '..', '..', '.temp', 'hub-check');
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));

// Engine replies. Two installs: a new one with nothing downloaded, and an
// existing one already using a model the guide does not list.
function setupStatus(existing) {
  const models = [
    {key: 'base', label: 'Base', bytes: 150e6, required_bytes: 550e6, hint: 'Small download and a practical starting point on CPU.', cached: false},
    {key: 'small', label: 'Small', bytes: 500e6, required_bytes: 1250e6, hint: 'More accurate; slower on CPU.', cached: false},
    {key: 'large-v3-turbo', label: 'Turbo', bytes: 1.7e9, required_bytes: 3.65e9, hint: 'Optional higher quality; a working GPU is recommended.', cached: existing},
  ];
  if (existing) models.unshift({key: 'kb-whisper-large', label: 'kb-whisper-large', bytes: 0, required_bytes: 0, hint: 'The model you use now.', cached: true, current: true});
  return {
    completed: existing, step: 0, model_key: existing ? 'kb-whisper-large' : 'base', recommended: 'base',
    models, free_bytes: 120e9, ready: existing,
    download: {state: 'idle', model_key: '', completed: 0, total: 0, unit: 'B', error: ''},
    mic_test: {active: false, tested: false, level: 0, peak: 0, heard: false, error: ''},
    mics: [{id: 1, name: 'Microphone (USB Audio Device)'}, {id: 2, name: 'Headset Microphone (Wireless)'}],
    mic: 1, languages: existing ? ['en', 'sv'] : [],
    language_options: [['en', 'English'], ['sv', 'Swedish'], ['de', 'German'], ['fr', 'French'], ['es', 'Spanish']].map(([value, label]) => ({value, label})),
    hotkeys: {record: 'Ctrl+Win', stop: 'Ctrl', cancel: 'Esc', mode: 'push_to_talk', double_tap: true},
  };
}

const STYLES = {
  styles: ['formal', 'casual', 'very_casual', 'verbatim'], default_style: 'casual',
  rules: [
    {id: 'chat', label: 'Chat apps', style: 'casual', instructions: '', apps: ['discord.exe', 'slack.exe', 'whatsapp.exe']},
    {id: 'ai', label: 'AI assistants', style: 'formal', instructions: '', apps: ['claude.exe', 'chatgpt.exe']},
    {id: 'browser', label: 'Browsers', style: '', instructions: '', apps: ['chrome.exe', 'msedge.exe', 'explorer.exe']},
  ],
};

const SETTINGS = {
  quality: 'balanced', keep_audio: true, mic: 1,
  mics: [{id: 1, name: 'Microphone (USB Audio Device)'}, {id: 2, name: 'Headset Microphone (Wireless)'}],
  ai_model: 'gemma3:4b', ai_models: ['gemma3:4b'], ai_reachable: true, about_me: '',
  idle_pill: true, sounds: true, retention_days: null, autostart_supported: true, autostart: true, update_checks: false,
};

// The stub bridge runs in the page; engine calls cross to this process.
const STUB = `
  const {ipcRenderer} = require('electron');
  window.hub = {
    call: (method, params) => ipcRenderer.invoke('stub:call', method, params || {}).then(reply => {
      if (reply.ok) return reply.result; throw new Error(reply.error);
    }),
    onNavigate: () => {}, onEvent: () => {}, copy: async () => {},
    appIcon: name => ipcRenderer.invoke('stub:icon', name),
    updateCheck: async () => ({}), openReleases: async () => {}, audioSource: async () => '',
    notes: {list: async () => [], save: async () => {}, trash: async () => {}, reveal: async () => {}},
  };`;

async function run() {
  const failures = [];
  let status = setupStatus(false);
  const calls = [];
  ipcMain.handle('stub:icon', (event, name) => appIcons.resolve(name));
  ipcMain.handle('stub:call', (event, method, params) => {
    calls.push(method);
    if (method === 'setup_status') return {ok: true, result: status};
    if (method === 'setup_save') { Object.assign(status, params.values); return {ok: true, result: status}; }
    if (method === 'setup_mic_test') return {ok: true, result: status.mic_test};
    if (method === 'styles') return {ok: true, result: STYLES};
    if (method === 'settings') return {ok: true, result: SETTINGS};
    if (method === 'overview') return {ok: false, error: 'not in this check'};
    return {ok: false, error: `unexpected call ${method}`};
  });

  const preload = path.join(app.getPath('temp'), 'syllune-hub-stub.js');
  fs.writeFileSync(preload, STUB);
  fs.mkdirSync(OUT, {recursive: true});
  const win = new BrowserWindow({width: 1040, height: 700, show: false,
    webPreferences: {preload, contextIsolation: false, sandbox: false, offscreen: true}});
  win.webContents.on('console-message', event => { if (event.level === 'error') { failures.push(`console: ${event.message}`); console.error(event.message); } });
  const js = code => win.webContents.executeJavaScript(code);
  const shot = async name => fs.writeFileSync(path.join(OUT, `${name}.png`), (await win.webContents.capturePage()).toPNG());
  const clickNext = () => js(`document.querySelector('.setup-nav .button.primary').click()`);

  // New install: every step, with the sidebar hidden throughout.
  await win.loadFile(path.join(__dirname, '..', 'hub', 'index.html'));
  await wait(700);
  for (let index = 0; index < 4; index++) {
    const view = await js(`({title: document.querySelector('.setup-title')?.textContent, count: document.querySelector('.setup-count')?.textContent,
      sidebar: getComputedStyle(document.querySelector('.sidebar')).display, next: document.querySelector('.setup-nav .button.primary').disabled})`);
    if (view.sidebar !== 'none') failures.push(`step ${index + 1}: sidebar shows`);
    if (view.count !== `Step ${index + 1} of 4`) failures.push(`step ${index + 1}: count reads ${view.count}`);
    await shot(`new-${index + 1}-${String(view.title).toLowerCase().replaceAll(' ', '-')}`);
    if (index === 3 && !view.next) failures.push('Finish is enabled without a ready model');
    if (index < 3) { await clickNext(); await wait(450); }
  }
  // A language chip appears when one is picked.
  await js(`document.querySelector('.setup-nav .button.quiet').click()`); await wait(300);
  await js(`document.querySelector('.setup-nav .button.quiet').click()`); await wait(300);
  await js(`(() => { const s = document.querySelector('.setup-group select'); s.value = 'sv'; s.dispatchEvent(new Event('change')); })()`);
  await wait(300);
  const chips = await js(`[...document.querySelectorAll('.setup-chips .chip')].map(c => c.firstChild.textContent)`);
  if (chips.join() !== 'Swedish') failures.push(`language chips ${JSON.stringify(chips)}`);
  await shot('new-2-microphone-language');

  // Existing install replaying the guide: its model is shown as in use and ready.
  status = setupStatus(true);
  await win.loadFile(path.join(__dirname, '..', 'hub', 'index.html'), {query: {page: 'onboarding'}});
  await wait(700);
  await clickNext(); await wait(350); await clickNext(); await wait(450);
  const model = await js(`({checked: document.querySelector('input[name="setup-model"]:checked')?.value,
    message: document.querySelector('.setup-model-status + .setup-hint')?.textContent,
    button: document.querySelector('.setup-model-status .button').hidden})`);
  if (model.checked !== 'kb-whisper-large' || !model.button || !/ready/.test(model.message)) failures.push(`existing model ${JSON.stringify(model)}`);
  await shot('existing-3-speech-model');

  // Styles: app icons resolve from the real installed apps.
  await js(`Hub.navigate('styles')`);
  await wait(4000);
  const badges = await js(`[...document.querySelectorAll('.app-badge')].map(b => b.dataset.name + ':' + (b.querySelector('img') ? 'icon' : 'letters'))`);
  console.log('badges', badges.join(' '));
  if (await js(`document.body.classList.contains('setup-mode')`)) failures.push('setup mode stayed on after leaving the guide');
  await shot('styles-icons');

  // Settings: three short groups, nothing more.
  await js(`Hub.navigate('settings')`);
  await wait(600);
  const settings = await js(`({groups: [...document.querySelectorAll('.settings-heading')].map(h => h.textContent),
    rows: document.querySelectorAll('.settings-section .row').length})`);
  console.log('settings', JSON.stringify(settings));
  if (settings.groups.join() !== 'Dictation,General,Privacy' || settings.rows > 10) failures.push(`settings ${JSON.stringify(settings)}`);
  await shot('settings');
  await js(`document.querySelector('.settings-footer').scrollIntoView()`); await wait(200);
  await shot('settings-end');

  console.log(failures.length ? 'FAIL\n' + failures.join('\n') : `PASS hub check, screenshots in ${OUT}`);
  app.exit(failures.length ? 1 : 0);
}

app.disableHardwareAcceleration();
app.whenReady().then(run).catch(error => { console.error(error); app.exit(1); });
