// pages/settings.js
// The few settings people actually change, in three groups: dictation,
// general and privacy. Each control writes through hub_api as soon as it
// changes. Everything else stays in the settings file (tray > Help).

'use strict';

Hub.pages.settings = (() => {
  const { el, api } = Hub;

  const set = (key, value) => api('setting_set', { key, value });

  // Quality is also the AI edit switch: Fast is Whisper alone.
  const QUALITY_HINTS = {
    fast: 'Quickest. Words are typed as spoken, without the AI edit.',
    balanced: 'A local AI fixes punctuation, drops filler words and follows each app’s style.',
    best: 'Like Balanced with the largest speech model. Slower, and needs a strong GPU.',
  };
  const RETENTION = [
    { value: null, label: 'Forever' },
    { value: 365, label: 'A year' },
    { value: 90, label: '90 days' },
    { value: 30, label: '30 days' },
    { value: 7, label: '7 days' },
    { value: 0, label: 'Off' },
  ];

  async function mount() {
    const s = await api('settings');
    return Hub.page('Settings', null,
      section('Dictation', [micRow(s), qualityRow(s), s.ai_models.length > 1 ? modelRow(s) : null, aboutRow(s)]),
      section('General', [
        row('Sounds', 'A soft sound when recording starts and stops.',
          Hub.toggle({ checked: s.sounds, label: 'Sounds', onChange: (v) => set('sounds', v) })),
        row('Resting pill', 'Keep a small pill on screen between dictations. Drag it to the bottom or either side.',
          Hub.toggle({ checked: s.idle_pill, label: 'Resting pill', onChange: (v) => set('idle_pill', v) })),
        s.autostart_supported
          ? row('Start on login', null, Hub.toggle({ checked: s.autostart, label: 'Start on login', onChange: (v) => set('autostart', v) }))
          : null,
        updatesRow(s),
      ]),
      section('Privacy', [historyRow(s),
        row('Save dictation audio', 'Keep recordings you can play back until you delete the dictation.',
          Hub.toggle({checked: s.keep_audio, label: 'Save dictation audio', onChange: v => set('keep_audio', v)})),
      ]),
      footer());
  }

  // --- Layout --------------------------------------------------------------

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

  // Rarely needed actions sit under the groups as quiet links.
  function footer() {
    const link = (text, onClick) => el('button', { class: 'entry-link', type: 'button', text, on: { click: onClick } });
    return el('div', { class: 'settings-footer' },
      el('span', { class: 'faint', text: 'Everything runs and stays on this PC.' }),
      link('Open data folder', () => api('open_folder', { which: 'data' }).catch(Hub.toastError)),
      link('Run setup again', () => Hub.navigate('onboarding')));
  }

  // --- Dictation -----------------------------------------------------------

  function micRow(s) {
    const options = s.mics.map((mic) => ({ value: mic.id, label: mic.name }));
    if (!options.some((option) => option.value === s.mic)) options.unshift({ value: s.mic, label: 'Windows default' });
    return row('Microphone', null, Hub.select({ options, value: s.mic, label: 'Microphone', onChange: (v) => set('mic', v) }));
  }

  // The hint also says when the AI edit can't run, so a choice never fails silently.
  function qualityRow(s) {
    const hint = el('div', { class: 'row-hint' });
    const paint = (value) => {
      const offline = value !== 'fast' && !s.ai_reachable;
      hint.textContent = offline ? 'Ollama isn’t running, so dictations are typed without the AI edit until it starts.' : QUALITY_HINTS[value] || '';
    };
    const control = Hub.segmented({
      options: [{ value: 'fast', label: 'Fast' }, { value: 'balanced', label: 'Balanced' }, { value: 'best', label: 'Best' }],
      value: s.quality, label: 'Quality', onChange: (v) => set('quality', v),
    });
    control.onPaint = paint;
    paint(s.quality);
    return row('Quality', hint, control.node);
  }

  // Only shown when Ollama has more than one model to choose from.
  function modelRow(s) {
    const names = s.ai_models.includes(s.ai_model) ? s.ai_models : [s.ai_model, ...s.ai_models];
    return row('AI model', null, Hub.select({
      options: names.map((name) => ({ value: name, label: name })),
      value: s.ai_model, label: 'AI model', onChange: (v) => set('ai_model', v),
    }));
  }

  // Saved a moment after you stop typing, and when you leave the field.
  function aboutRow(s) {
    const textarea = el('textarea', {
      class: 'textarea', maxLength: 1500, rows: 3, spellcheck: 'true',
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
        el('div', { class: 'row-hint', text: 'Your work, names and how you like things written, so the AI edit gets them right.' })),
      textarea);
  }

  // --- General -------------------------------------------------------------

  // One row: the daily check, plus a button for checking right now.
  function updatesRow(s) {
    const status = el('div', { class: 'row-hint', role: 'status', text: 'Checks once a day. Audio and transcripts never leave this PC.' });
    const open = el('button', { class: 'button quiet', type: 'button', text: 'View releases', hidden: true,
      on: { click: () => window.hub.openReleases().catch(Hub.toastError) } });
    const check = el('button', { class: 'button quiet', type: 'button', text: 'Check now', on: { click: async () => {
      check.disabled = true; status.textContent = 'Checking…';
      try {
        const result = await window.hub.updateCheck();
        status.textContent = result.message || (result.available ? `Version ${result.latest} is out. Download it from releases.` : `You’re up to date (${result.current}).`);
        open.hidden = !result.url;
      } catch (error) { status.textContent = error.message; }
      finally { check.disabled = false; }
    } } });
    return row('Check for updates', status, [open, check,
      Hub.toggle({ checked: s.update_checks, label: 'Check for updates daily', onChange: (v) => set('update_checks', v) })]);
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

  // Pending text saves run before the page goes away.
  const flushers = [];

  return {
    title: 'Settings',
    mount: () => { flushers.length = 0; return mount(); },
    leave: () => flushers.forEach((flush) => flush()),
  };
})();
