// onboarding.js
// The setup guide: four short steps (welcome, microphone, speech model, try
// it), shown full-window on first run and from Settings. Choices save as the
// user makes them; downloads and the microphone test only start on a click.
// Polling repaints status nodes only, so focus and typed text are kept.
'use strict';

Hub.pages.onboarding = (() => {
  const { el, api } = Hub;
  const STEPS = ['Welcome', 'Microphone', 'Speech model', 'Try it'];
  const LAST = STEPS.length - 1;
  const POLL_MS = 700;
  const POLL_MIC_MS = 100;            // the level bar needs a lively feed

  let current, step, root, stepNode, notice, back, next;
  let timer = null;
  let gone = false;
  let polling = false;
  let pending = false;
  let revision = 0;
  let scratch = '';
  // Status nodes the poll repaints, set by whichever step is showing.
  let view = {};

  const bytes = n => n >= 1e9 ? `${(n / 1e9).toFixed(1)} GB` : `${Math.ceil(n / 1e6)} MB`;
  const keycaps = combo => el('span', { class: 'setup-keys' },
    String(combo).split('+').map(key => el('kbd', { text: key.trim().replace(/^./, c => c.toUpperCase()) })));
  const lead = text => el('p', { class: 'setup-lead', text });

  // =========================================================================
  // Saving
  // =========================================================================

  // One change at a time; a failed write shows on the step that owns it.
  async function run(action) {
    if (pending || gone) return;
    pending = true;
    revision++;
    notice.textContent = '';
    paint();
    try { await action(); }
    catch (error) { if (!gone) notice.textContent = error.message || String(error); }
    finally {
      pending = false;
      if (!gone) paint();
    }
  }

  async function save(values) {
    current = await api('setup_save', { values });
  }

  async function goTo(to) {
    await api('setup_mic_test', { action: 'stop' });
    await save({ step: to });
    step = to;
    renderStep();
  }

  // =========================================================================
  // Steps
  // =========================================================================

  function renderStep() {
    view = {};
    const builders = [welcome, microphone, models, tryIt];
    stepNode = el('section', { class: 'setup-step', 'aria-labelledby': 'setup-title' });
    builders[step]();
    root.querySelector('.setup-body').replaceChildren(stepNode);
    root.querySelector('.setup-count').textContent = `Step ${step + 1} of ${STEPS.length}`;
    root.querySelectorAll('.setup-progress span').forEach((segment, index) => {
      segment.classList.toggle('done', index <= step);
    });
    next.textContent = step === 0 ? 'Get started' : step === LAST ? 'Finish' : 'Continue';
    back.hidden = step === 0;
    paint();
    stepNode.querySelector('h1').focus();
  }

  const title = text => el('h1', { id: 'setup-title', class: 'setup-title', tabIndex: -1, text });

  function welcome() {
    const keys = current.hotkeys;
    stepNode.append(
      el('img', { class: 'setup-mark', src: '../assets/syllune.svg', alt: '', width: 56, height: 56 }),
      title('Welcome to Syllune'),
      lead('Speak, and your words appear wherever you are typing. Speech recognition runs on this computer, so your voice stays here.'),
      el('ul', { class: 'setup-points' },
        el('li', {}, el('span', { text: keys.mode === 'push_to_talk' ? 'Hold' : 'Press' }), keycaps(keys.record), el('span', { text: 'and talk' })),
        el('li', { text: 'Works in any app with a text field' }),
        el('li', { text: 'Takes about two minutes to set up' })));
  }

  function microphone() {
    // Ids are "default" or the microphone's name (see microphone_choices in the engine).
    const devices = [...current.mics];
    const select = el('select', { class: 'select', id: 'setup-mic' });
    for (const item of devices) select.append(el('option', { value: String(item.id), text: item.name }));
    select.value = String(current.mic);
    select.addEventListener('change', () => run(async () => {
      await api('setup_mic_test', { action: 'stop' });
      const device = devices.find(item => String(item.id) === select.value);
      try { await save({ mic: device.id }); }
      finally { select.value = String(current.mic); }
    }));

    view.micButton = el('button', { type: 'button', class: 'button', on: { click: () => run(async () => {
      await api('setup_mic_test', { action: current.mic_test.active ? 'stop' : 'start' });
      current = await api('setup_status');
      restartPoll();
    }) } });
    view.micFill = el('span', { class: 'setup-level-fill' });
    view.micMessage = el('p', { class: 'setup-hint', role: 'status' });

    stepNode.append(title('Your microphone'),
      lead('Pick the microphone you talk into. The test only shows your input level; nothing is recorded or saved.'),
      el('label', { class: 'setup-label', for: 'setup-mic', text: 'Microphone' }), select,
      el('div', { class: 'setup-level-row' }, view.micButton,
        el('span', { class: 'setup-level', role: 'presentation' }, view.micFill)),
      view.micMessage,
      languages());
  }

  // Selected languages as removable chips plus one picker, instead of a grid
  // of a hundred checkboxes. None selected means any language is detected.
  function languages() {
    const names = new Map(current.language_options.map(item => [item.value, item.label]));
    const chips = el('div', { class: 'setup-chips' });
    const picker = el('select', { class: 'select', 'aria-label': 'Add a language' });
    const apply = codes => run(async () => { await save({ languages: codes }); paintLanguages(); });
    const paintLanguages = () => {
      chips.replaceChildren(...current.languages.map(code => el('span', { class: 'chip' }, names.get(code) || code,
        el('button', { type: 'button', class: 'chip-remove', 'aria-label': `Remove ${names.get(code) || code}`, text: '×',
          on: { click: () => apply(current.languages.filter(item => item !== code)) } }))));
      if (!current.languages.length) chips.append(el('span', { class: 'setup-hint', text: 'Any language' }));
      picker.replaceChildren(el('option', { value: '', text: 'Add a language…' }),
        ...[...names].filter(([code]) => !current.languages.includes(code))
          .sort((a, b) => a[1].localeCompare(b[1])).map(([code, label]) => el('option', { value: code, text: label })));
      picker.value = '';
    };
    picker.addEventListener('change', () => { if (picker.value) apply([...current.languages, picker.value]); });
    paintLanguages();
    return el('div', { class: 'setup-group' },
      el('span', { class: 'setup-label', text: 'Languages you speak' }), chips, picker,
      el('p', { class: 'setup-hint', text: 'Choosing your languages makes short dictations more accurate. Leave it empty to detect any language.' }));
  }

  function models() {
    const choices = el('div', { class: 'setup-choices', role: 'radiogroup', 'aria-label': 'Speech model' });
    for (const item of current.models) {
      const tags = [item.current && 'In use', item.key === current.recommended && 'Recommended', !item.current && item.cached && 'Downloaded'].filter(Boolean);
      const input = el('input', { type: 'radio', name: 'setup-model', value: item.key, checked: item.key === current.model_key });
      input.addEventListener('change', () => run(async () => {
        try { await save({ model_key: item.key }); }
        finally { renderStep(); }
      }));
      choices.append(el('label', { class: 'setup-choice' }, input,
        el('span', { class: 'setup-choice-text' },
          el('span', { class: 'setup-choice-name' }, item.label, ...tags.map(tag => el('span', { class: 'setup-tag', text: tag }))),
          el('span', { class: 'setup-hint', text: item.current ? item.hint : `${item.hint} About ${bytes(item.bytes)}.` }))));
    }
    view.modelFill = el('span', { class: 'setup-level-fill' });
    view.modelBar = el('span', { class: 'setup-level setup-progress-bar' }, view.modelFill);
    view.modelMessage = el('p', { class: 'setup-hint', role: 'status' });
    view.modelButton = el('button', { type: 'button', class: 'button', on: { click: () => run(async () => {
      current = await api('setup_download', { model_key: current.model_key });
    }) } });
    stepNode.append(title('Speech model'),
      lead('This model turns your speech into text on this computer. Larger models are more accurate and want a faster computer.'),
      choices,
      el('div', { class: 'setup-model-status' }, view.modelButton, view.modelBar),
      view.modelMessage);
  }

  function tryIt() {
    const keys = current.hotkeys;
    const input = el('textarea', { class: 'textarea setup-scratch', rows: 4, 'aria-label': 'Practice here',
      placeholder: 'Click here, then dictate…' });
    input.value = scratch;
    input.addEventListener('input', () => { scratch = input.value; });
    const how = keys.mode === 'push_to_talk'
      ? [el('span', { text: 'Click the box, then hold' }), keycaps(keys.record), el('span', { text: 'while you say a sentence.' })]
      : [el('span', { text: 'Click the box, press' }), keycaps(keys.record), el('span', { text: 'and speak, then press' }), keycaps(keys.stop), el('span', { text: 'to finish.' })];
    const tips = [
      keys.double_tap && el('li', {}, el('span', { text: 'Double-tap' }), keycaps(keys.record), el('span', { text: 'to keep recording hands-free.' })),
      el('li', {}, el('span', { text: 'Press' }), keycaps(keys.cancel), el('span', { text: 'to cancel a recording.' })),
      el('li', { text: 'Drag the pill to the bottom or either side of the screen.' }),
    ];
    view.finishHint = el('p', { class: 'setup-hint', role: 'status' });
    stepNode.append(title('Try it'), el('p', { class: 'setup-lead setup-how' }, how), input,
      el('ul', { class: 'setup-tips' }, tips), view.finishHint);
  }

  // =========================================================================
  // Live status
  // =========================================================================

  function paint() {
    if (gone || !current || !next) return;
    const transfer = current.download;
    const busy = ['downloading', 'loading'].includes(transfer.state);
    back.disabled = pending;
    next.disabled = pending || (step === LAST && !current.ready);
    for (const control of stepNode.querySelectorAll('input, select, button, textarea')) {
      if (control !== view.modelButton && control !== view.micButton) control.disabled = pending && control.tagName !== 'TEXTAREA';
    }

    if (view.micButton) {
      const mic = current.mic_test;
      view.micButton.disabled = pending;
      view.micButton.textContent = mic.active ? 'Stop test' : mic.error ? 'Try again' : 'Test microphone';
      view.micFill.style.transform = `scaleX(${mic.active ? Math.min(1, Math.sqrt(mic.level) * 1.4) : 0})`;
      view.micMessage.textContent = mic.error || (mic.active ? 'Say something. The test stops by itself after 15 seconds.'
        : mic.heard ? 'Your microphone works.' : mic.tested ? 'Nothing heard. Check that this is the microphone you talk into and that it is not muted.' : '');
    }

    if (view.modelButton) {
      const model = current.models.find(item => item.key === current.model_key) || current.models[0];
      view.modelButton.hidden = current.ready;
      view.modelButton.disabled = pending || busy;
      view.modelButton.textContent = busy ? 'Preparing…' : transfer.state === 'error' ? 'Try again' : model.cached ? 'Use this model' : `Download ${model.label}`;
      view.modelBar.hidden = !busy;
      const share = transfer.total > 0 ? transfer.completed / transfer.total : 0;
      view.modelFill.style.transform = `scaleX(${transfer.state === 'loading' ? 1 : share})`;
      const space = current.free_bytes === null ? '' : ` ${bytes(current.free_bytes)} free on this drive.`;
      view.modelMessage.textContent = transfer.state === 'error' ? transfer.error
        : current.ready ? `✓ ${model.label} is ready.`
          : busy ? (transfer.state === 'loading' ? 'Loading the model…' : transfer.total > 0 ? `Downloading… ${Math.round(100 * share)}%` : 'Connecting…')
            : model.cached ? 'Already on this computer.' : `Downloads once from Hugging Face, then works offline.${space}`;
      for (const input of stepNode.querySelectorAll('input[name="setup-model"]')) input.disabled = pending || busy;
    }

    if (view.finishHint) {
      view.finishHint.textContent = current.ready ? 'You can open this guide again from Settings.'
        : 'Your speech model is not ready yet. Go back to Speech model to finish setting it up.';
    }
  }

  async function poll() {
    if (gone || polling || pending) return;
    polling = true;
    const asked = revision;
    try {
      const status = await api('setup_status');
      if (!gone && !pending && asked === revision) {
        const micWasActive = current.mic_test.active;
        current = status;
        paint();
        if (micWasActive !== status.mic_test.active) restartPoll();
      }
    }
    catch (error) { if (!gone) notice.textContent = `Reconnecting… ${error.message || error}`; }
    finally { polling = false; }
  }

  function restartPoll() {
    clearInterval(timer);
    if (!gone) timer = setInterval(poll, current?.mic_test.active ? POLL_MIC_MS : POLL_MS);
  }

  // =========================================================================
  // Page
  // =========================================================================

  async function mount() {
    gone = false;
    pending = false;
    scratch = '';
    current = await api('setup_status');
    step = current.completed ? 0 : Math.min(LAST, current.step);
    document.body.classList.add('setup-mode');
    notice = el('p', { class: 'setup-error', role: 'alert' });
    back = el('button', { type: 'button', class: 'button quiet', text: 'Back', on: { click: () => run(() => goTo(step - 1)) } });
    next = el('button', { type: 'button', class: 'button primary', on: { click: () => run(async () => {
      if (step < LAST) return goTo(step + 1);
      await api('setup_complete');
      await Hub.navigate('home');
    }) } });
    const close = el('button', { type: 'button', class: 'button quiet setup-close', text: current.completed ? 'Close' : 'Set up later',
      on: { click: () => Hub.navigate('home') } });
    root = el('div', { class: 'setup' },
      close,
      el('div', { class: 'setup-card' },
        el('div', { class: 'setup-progress', 'aria-hidden': 'true' }, STEPS.map(() => el('span'))),
        el('p', { class: 'setup-count' }),
        el('div', { class: 'setup-body' }),
        notice,
        el('div', { class: 'setup-nav' }, back, next)));
    renderStep();
    return root;
  }

  return {
    title: 'Set up Syllune', mount,
    mounted: () => {
      restartPoll();
      stepNode?.querySelector('h1')?.focus();
    },
    leave: () => {
      gone = true;
      clearInterval(timer);
      scratch = '';
      document.body.classList.remove('setup-mode');
      api('setup_mic_test', { action: 'stop' }).catch(() => {});
    },
  };
})();
