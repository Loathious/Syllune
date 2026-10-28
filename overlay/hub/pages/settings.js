// pages/settings.js
// The settings people actually change: quality and microphone, the AI edit,
// the pill, history and a few system options. Each control writes through
// hub_api as soon as it changes. Everything else stays in Advanced settings
// (tray menu), which edits the full settings file.

'use strict';

Hub.pages.settings = (() => {
  const { el, api } = Hub;

  const set = (key, value) => api('setting_set', { key, value });

  const QUALITY_HINTS = {
    fast: 'Quickest. Whisper alone, no AI edit.',
    balanced: 'Accurate, with the AI edit for punctuation and tone. About a second or two.',
    best: 'Largest model for English. Slower, and uses more video memory.',
  };
  const RETENTION = [
    { value: null, label: 'Forever' },
    { value: 365, label: 'A year' },
    { value: 90, label: '90 days' },
    { value: 30, label: '30 days' },
    { value: 7, label: '7 days' },
    { value: 0, label: 'Off' },
  ];

  const POSITION_LABELS = { 'bottom-center': 'Bottom center', 'middle-left': 'Left side', 'middle-right': 'Right side' };
  const positionLabel = (name) => POSITION_LABELS[name] || name;

  async function mount() {
    const s = await api('settings');
    return Hub.page('Settings', null,
      section('Dictation', [qualityRow(s),
        row('Quick short dictations', 'Clips up to two seconds use faster decoding and skip the AI rewrite.',
          Hub.toggle({checked: s.quick_short_clips, label: 'Quick short dictations', onChange: v => set('quick_short_clips', v)})), micRow(s)]),
      section('AI edit', [aiRow(s), modelRow(s), aboutRow(s)]),
      section('Pill', [
        row('Resting pill', 'A small pill stays on screen between dictations.',
          Hub.toggle({ checked: s.idle_pill, label: 'Resting pill', onChange: (v) => set('idle_pill', v) })),
        row('Position', null, Hub.select({
          options: s.positions.map((p) => ({ value: p, label: positionLabel(p) })),
          value: s.position, label: 'Pill position', onChange: (v) => set('position', v),
        })),
        row('Sounds', 'A short sound when recording starts and stops.',
          Hub.toggle({ checked: s.sounds, label: 'Sounds', onChange: (v) => set('sounds', v) })),
      ]),
      section('Privacy', [historyRow(s),
        row('Save dictation audio', 'Keep playable recordings until you delete the dictation. Meetings also retain their audio.',
          Hub.toggle({checked: s.keep_audio, label: 'Save dictation audio', onChange: v => set('keep_audio', v)})),
        row('Your data', 'History, stats and settings, all in one folder on this PC.',
          el('button', { class: 'button', type: 'button', on: { click: () => api('open_folder', { which: 'data' }).catch(Hub.toastError) } },
            Hub.icon('folder'), 'Open folder')),
      ]),
      section('System', [
        s.autostart_supported
          ? row('Start on login', null, Hub.toggle({ checked: s.autostart, label: 'Start on login', onChange: (v) => set('autostart', v) }))
          : null,
        typingRow(s),
      ]),
      section('Setup and updates', [
        row('Set up Syllune', 'Microphone, models, writing style and a place to try dictation.',
          el('button', {class: 'button', type: 'button', text: 'Open setup guide', on: {click: () => Hub.navigate('onboarding')}})),
        row('Check for updates daily', 'Checks Syllune’s public repository. Audio and transcripts stay here.',
          Hub.toggle({checked: s.update_checks, label: 'Check for updates daily', onChange: v => set('update_checks', v)})),
        updatesRow(),
      ]),
      el('p', { class: 'faint', style: { 'margin-top': '28px', 'text-align': 'center' }, text: 'Everything runs and stays on this PC.' }));
  }

  // --- Layout --------------------------------------------------------------

  function updatesRow() {
    const status = el('div', {class: 'row-hint', role: 'status', text: 'Install updates from Syllune’s repository releases.'});
    const open = el('button', {class: 'button quiet', type: 'button', text: 'View releases', hidden: true,
      on: {click: () => window.hub.openReleases().catch(Hub.toastError)}});
    const check = el('button', {class: 'button', type: 'button', text: 'Check now', on: {click: async () => {
      check.disabled = true; status.textContent = 'Checking…';
      try {
        const result = await window.hub.updateCheck();
        status.textContent = result.message || (result.available ? `Update ${result.latest} is ready. Download its installer from releases.` : `You’re up to date (${result.current}).`);
        open.hidden = !result.url;
      } catch (error) { status.textContent = error.message; }
      finally { check.disabled = false; }
    }}});
    return row('Repository updates', status, [check, open]);
  }

  function section(title, rows) {
    return el('section', { class: 'settings-section' },
      el('h2', { class: 'settings-heading', text: title }),
      el('div', { class: 'panel' }, rows));
  }

  // `hint` may be a node, so a row can update its own hint.
  function row(label, hint, control, stacked = false) {
    return el('div', { class: stacked ? 'row stacked' : 'row' },
      el('div', { class: 'row-text' },
        el('div', { class: 'row-label', text: label }),
        hint ? (hint instanceof Node ? hint : el('div', { class: 'row-hint', text: hint })) : null),
      el('div', { class: 'row-control' }, control));
  }

  // --- Dictation -----------------------------------------------------------

  function qualityRow(s) {
    const hint = el('div', { class: 'row-hint', text: QUALITY_HINTS[s.quality] || '' });
    const control = Hub.segmented({
      options: [{ value: 'fast', label: 'Fast' }, { value: 'balanced', label: 'Balanced' }, { value: 'best', label: 'Best' }],
      value: s.quality, label: 'Quality', onChange: (v) => set('quality', v),
    });
    control.onPaint = (value) => { hint.textContent = QUALITY_HINTS[value] || ''; };
    return row('Quality', hint, control.node);
  }

  function micRow(s) {
    const options = s.mics.map((mic) => ({ value: mic.id, label: mic.name }));
    if (!options.some((option) => option.value === s.mic)) options.unshift({ value: s.mic, label: 'Windows default' });
    return row('Microphone', null, Hub.select({ options, value: s.mic, label: 'Microphone', onChange: (v) => set('mic', v) }));
  }

  // --- AI edit -------------------------------------------------------------

  function aiRow(s) {
    return row('Clean up with AI',
      'A local model fixes punctuation, drops filler words and follows each app’s style. It runs on your GPU and unloads while you game.',
      Hub.toggle({ checked: s.ai_enabled, label: 'Clean up with AI', onChange: (v) => set('ai_enabled', v) }));
  }

  function modelRow(s) {
    let status;
    if (!s.ai_reachable) status = el('div', { class: 'row-hint' }, el('span', { class: 'dot off', style: { display: 'inline-block', 'margin-right': '6px' } }), "Ollama isn't running. Start it to use the AI edit.");
    else if (!s.ai_models.length) status = el('div', { class: 'row-hint', text: 'No models installed in Ollama yet.' });
    else status = el('div', { class: 'row-hint' }, el('span', { class: 'dot on', style: { display: 'inline-block', 'margin-right': '6px' } }), 'Ollama is running');
    const names = s.ai_models.includes(s.ai_model) ? s.ai_models : [s.ai_model, ...s.ai_models];
    return row('Model', status, Hub.select({
      options: names.map((name) => ({ value: name, label: name })),
      value: s.ai_model, label: 'AI model', disabled: !s.ai_models.length,
      onChange: (v) => set('ai_model', v),
    }));
  }

  // Saved a moment after you stop typing, and when you leave the field.
  function aboutRow(s) {
    const textarea = el('textarea', {
      class: 'textarea', maxLength: 1500, rows: 4, spellcheck: 'true',
      placeholder: 'For example: I write short project updates. Names I use often: ...',
      'aria-label': 'About you',
    });
    textarea.value = s.about_me;
    let saved = s.about_me;
    const savedNote = el('span', { class: 'faint saved-note', text: 'Saved' });
    const save = Hub.debounce(async () => {
      const value = textarea.value.trim();
      if (value === saved) return;
      try {
        await set('about_me', value);
        saved = value;
        savedNote.classList.add('shown');
        setTimeout(() => savedNote.classList.remove('shown'), 1400);
      } catch (error) {
        Hub.toastError(error);
      }
    }, 900);
    textarea.addEventListener('input', save);
    textarea.addEventListener('blur', save.flush);
    flushers.push(save.flush);
    return el('div', { class: 'row stacked' },
      el('div', { class: 'row-text' },
        el('div', { class: 'row-label' }, 'About you ', savedNote),
        el('div', { class: 'row-hint', text: 'Context the AI edit should know: your work, names, how you like things written.' })),
      textarea);
  }

  // --- Privacy -------------------------------------------------------------

  function historyRow(s) {
    const options = [...RETENTION];
    if (!options.some((option) => option.value === s.retention_days)) {
      options.splice(1, 0, { value: s.retention_days, label: `${s.retention_days} days` });
    }
    return row('Keep history', 'Older dictations are removed from this PC.', Hub.select({
      options, value: s.retention_days, label: 'Keep history', onChange: (v) => set('retention_days', v),
    }));
  }

  // --- System --------------------------------------------------------------

  function typingRow(s) {
    const input = el('input', {
      class: 'input tabular', type: 'number', min: '10', max: '250', step: '1',
      'aria-label': 'Typing speed in words per minute', style: { width: '76px' },
    });
    input.value = String(s.typing_wpm);
    let saved = s.typing_wpm;
    const save = Hub.debounce(async () => {
      const value = Math.round(Number(input.value));
      if (value === saved) return;
      if (!(value >= 10 && value <= 250)) {
        input.value = String(saved);
        Hub.toast('Typing speed is between 10 and 250 words per minute');
        return;
      }
      try {
        await set('typing_wpm', value);
        saved = value;
      } catch (error) {
        input.value = String(saved);
        Hub.toastError(error);
      }
    }, 700);
    input.addEventListener('input', save);
    input.addEventListener('blur', save.flush);
    flushers.push(save.flush);
    return row('Typing speed', 'Used to work out how much time dictation saves you.',
      el('span', { class: 'row-control' }, input, el('span', { class: 'muted', text: 'wpm' })));
  }

  // Pending text saves run before the page goes away.
  const flushers = [];

  return {
    title: 'Settings',
    mount: () => { flushers.length = 0; return mount(); },
    leave: () => flushers.forEach((flush) => flush()),
  };
})();
