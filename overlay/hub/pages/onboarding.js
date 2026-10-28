// First-run guide and Settings replay, backed by the engine's setup RPCs.
// Downloads and microphone access require explicit buttons. Progress polling
// updates only status nodes, preserving keyboard focus and the scratch field.
'use strict';

Hub.pages.onboarding = (() => {
  const { el, api } = Hub;
  const STEPS = ['Welcome', 'Your voice', 'Speech model', 'Writing', 'Controls & privacy', 'Try it'];
  const SAMPLE = 'please review the draft and send your feedback by friday';
  const EXAMPLES = {
    formal: 'Please review the draft and send your feedback by Friday.',
    casual: 'Please review the draft and send your feedback by Friday',
    verbatim: SAMPLE,
    prompt: 'Please review the draft and send your feedback by Friday.',
    very_casual: SAMPLE,
  };
  let current, settings, styles, step, root, body, notice, back, next, later, heading;
  let timer = null;
  let gone = false;
  let polling = false;
  let pending = false;
  let revision = 0;
  let scratch = '';
  let modelProgress, modelMessage, modelButton, finishHint, micMeter, micMessage, micButton;

  const bytes = n => n >= 1e9 ? `${(n / 1e9).toFixed(1)} GB` : `${Math.ceil(n / 1e6)} MB`;
  const button = (label, action, primary = false) => el('button', {
    type: 'button', class: `button${primary ? ' primary' : ''}`, text: label,
    on: { click: () => action() },
  });
  const paragraph = text => el('p', { class: 'setup-copy', text });
  const label = (title, control, hint) => el('label', { class: 'setup-field' },
    el('span', { class: 'setup-label', text: title }), control,
    hint ? el('span', { class: 'row-hint', text: hint }) : null);

  // Keep failed writes visible on the step that owns the choice.
  async function run(action) {
    if (pending || gone) return;
    pending = true;
    revision++;
    notice.textContent = '';
    next.disabled = back.disabled = later.disabled = true;
    for (const input of body.querySelectorAll('input, select, textarea, button')) input.disabled = true;
    try { await action(); }
    catch (error) { if (!gone) notice.textContent = error.message || String(error); }
    finally {
      pending = false;
      if (!gone) {
        for (const input of body.querySelectorAll('input, select, textarea, button')) input.disabled = false;
        paintStatus();
      }
    }
  }

  async function save(values) {
    current = await api('setup_save', { values });
    paintStatus();
  }

  // Settings controls use the existing setter and restore their UI on errors.
  function option(title, hint, key) {
    const input = el('input', { type: 'checkbox', checked: !!settings[key] });
    input.addEventListener('change', () => {
      const value = input.checked;
      input.disabled = true;
      run(async () => {
        try { await api('setting_set', { key, value }); settings[key] = value; }
        finally { input.checked = !!settings[key]; input.disabled = false; }
      });
    });
    return el('label', { class: 'setup-option' }, input,
      el('span', {}, el('strong', { text: title }), el('span', { class: 'row-hint', text: hint })));
  }

  async function move(to) {
    await api('setup_mic_test', { action: 'stop' });
    await save({ step: to });
    step = to;
    renderStep();
  }

  function renderStep() {
    modelProgress = modelMessage = modelButton = finishHint = micMeter = micMessage = micButton = null;
    heading.textContent = STEPS[step];
    body.replaceChildren();
    const progress = root.querySelector('.setup-steps');
    progress.replaceChildren(...STEPS.map((name, index) => el('li', {
      class: index === step ? 'current' : '', 'aria-current': index === step ? 'step' : null,
    }, el('span', { class: 'setup-step-number', text: String(index + 1) }), name)));
    [welcome, voice, models, writing, controls, tryIt][step]();
    next.textContent = step === 5 ? 'Finish setup' : 'Continue';
    later.textContent = step === 5 ? 'Return to model setup' : 'Do this later';
    paintStatus();
    heading.focus();
  }

  function welcome() {
    body.append(el('div', { class: 'setup-wave', 'aria-hidden': 'true' }, '∿'),
      el('h2', { text: 'Speak. Keep your words close.' }),
      paragraph('Syllune turns your voice into text on this computer. Your microphone and a local speech model are all you need.'),
      paragraph('The floating waveform pill rests at the bottom of your screen. Drag it to the left, right or bottom dock whenever you need more room.'),
      el('div', { class: 'setup-note' }, 'This guide takes a few minutes. Your choices are saved as you go, so you can leave and return. Speech-model downloads start only when you choose Download.'));
  }

  function voice() {
    const microphone = el('select', { class: 'select', 'aria-label': 'Microphone' });
    const devices = [...current.mics];
    if (!devices.some(item => String(item.id) === String(current.mic))) {
      devices.unshift({ id: current.mic, name: 'System default' });
    }
    for (const item of devices) microphone.append(el('option', { value: String(item.id), text: item.name }));
    microphone.value = String(current.mic);
    microphone.addEventListener('change', () => run(async () => {
      await api('setup_mic_test', { action: 'stop' });
      const device = devices.find(item => String(item.id) === microphone.value);
      try { await save({ mic: device.id }); }
      finally { microphone.value = String(current.mic); }
    }));
    micMeter = el('meter', { min: '0', max: '1', value: '0', 'aria-label': 'Microphone input level' });
    micMessage = el('p', { class: 'row-hint', role: 'status' });
    micButton = button('Start microphone test', () => run(async () => {
      await api('setup_mic_test', { action: current.mic_test.active ? 'stop' : 'start' });
      current = await api('setup_status');
    }));
    const languages = el('fieldset', { class: 'setup-languages' },
      el('legend', { text: 'Languages you speak' }));
    for (const item of [...current.language_options].sort((a, b) => a.label.localeCompare(b.label))) {
      const input = el('input', { type: 'checkbox', value: item.value,
        checked: current.languages.includes(item.value) });
      input.addEventListener('change', () => run(async () => {
        const codes = [...languages.querySelectorAll('input:checked')].map(node => node.value);
        try { await save({ languages: codes }); }
        finally {
          for (const field of languages.querySelectorAll('input')) field.checked = current.languages.includes(field.value);
        }
      }));
      languages.append(el('label', {}, input, item.label));
    }
    body.append(label('Microphone', microphone),
      el('div', { class: 'setup-mic' }, micButton, micMeter, micMessage),
      paragraph('The test checks input level for up to 15 seconds. It does not transcribe or save audio. Speak only when you choose to start; Stop ends it immediately.'),
      languages, paragraph('Select your spoken languages to improve short dictations. Leave all unchecked to allow automatic detection of any language. You can skip the microphone test.'));
  }

  function models() {
    body.append(paragraph('Choose one local speech model. Base is the small CPU-friendly starting point; AI editing and Ollama are optional and can be added later in Settings.'));
    const choices = el('fieldset', { class: 'setup-models' }, el('legend', { text: 'Speech model' }));
    for (const item of current.models) {
      const input = el('input', { type: 'radio', name: 'setup-model', value: item.key,
        checked: item.key === current.model_key });
      input.addEventListener('change', () => run(async () => {
        try { await save({ model_key: item.key }); }
        finally { renderStep(); }
      }));
      choices.append(el('label', { class: 'setup-model' }, input,
        el('span', {}, el('strong', { text: `${item.label}${item.key === current.recommended ? ' · Recommended' : ''}` }),
          el('span', { class: 'row-hint', text: `${item.hint} About ${bytes(item.bytes)}${item.cached ? ' · Already on this PC' : ''}` }))));
    }
    modelProgress = el('progress', { max: '100', 'aria-label': 'Model download progress' });
    modelMessage = el('p', { class: 'setup-transfer', role: 'status' });
    modelButton = button('Download selected model', () => run(async () => {
      current = await api('setup_download', { model_key: current.model_key });
    }), true);
    body.append(choices, el('div', { class: 'setup-model-status' }, modelProgress, modelMessage, modelButton),
      el('p', { class: 'row-hint', text: 'The selected files come from Hugging Face. Downloaded parts are reused after an interruption. Once installed, speech recognition works offline.' }));
  }

  function writing() {
    const select = el('select', { class: 'select', 'aria-label': 'Default writing style' });
    for (const name of styles.styles) select.append(el('option', { value: name, text: name.replaceAll('_', ' ') }));
    select.value = styles.default_style || 'casual';
    const preview = el('blockquote', { class: 'setup-example' });
    const hint = el('p', { class: 'row-hint' });
    const paint = () => {
      preview.textContent = EXAMPLES[select.value] || SAMPLE;
      hint.textContent = select.value === 'prompt'
        ? 'Prompt keeps a clear request. Structuring longer requests into requirements needs optional local AI editing.'
        : select.value === 'verbatim'
          ? 'Verbatim leaves wording, capitals and punctuation alone. Spoken formatting commands and your saved corrections still apply.'
          : 'Examples illustrate the built-in style presets. Your actual result depends on speech recognition and your Settings.';
    };
    select.addEventListener('change', () => run(async () => {
      try { styles = await api('style_set', { rule_id: 'default', style: select.value }); }
      finally { select.value = styles.default_style || 'casual'; paint(); }
    }));
    // Choosing the suggestion is explicit; opening this step does not change
    // an existing user's global style during a replay.
    if (!styles.default_style) select.prepend(el('option', { value: '', text: 'Keep current formatting' }));
    select.value = styles.default_style || '';
    paint();
    body.append(paragraph('Choose how your words look by default. Styles for individual apps can override this choice from the Styles page.'),
      label('Default style', select), el('span', { class: 'setup-label', text: 'Same spoken sample' }),
      paragraph(SAMPLE), preview, hint,
      el('div', { class: 'setup-note' }, 'Local speech recognition works without Ollama. Formal, casual and verbatim formatting do not require an AI model. AI editing, personal context and longer prompt structure remain optional.'));
  }

  function controls() {
    const keys = current.hotkeys;
    const instructions = [
      keys.mode === 'push_to_talk' ? `Hold ${keys.record} to talk; release to insert your words.` : `Press ${keys.record} to start recording, then ${keys.stop} to stop.`,
      keys.double_tap ? `Double-tap ${keys.record} for hands-free recording; tap once more to finish.` : 'Enable double-tap hands-free recording in Advanced settings if you want it.',
      `Press ${keys.cancel} to cancel a recording.`,
      'Drag the pill to a highlighted dock and release to snap it there.',
      'If no editable field is ready, your words stay pending. Focus a field and use Insert from the pill or History.',
      'History lets you review, replay saved audio, correct words or delete a dictation.',
    ];
    body.append(el('ul', { class: 'setup-controls' }, instructions.map(text => el('li', { text }))),
      option('Save dictation audio', 'Keep playable audio with history. Meetings save their audio separately.', 'keep_audio'),
      option('Recording sounds', 'A short sound when recording starts and stops.', 'sounds'),
      option('Check for updates', 'Check the Syllune repository for releases. Model downloads are separate.', 'update_checks'));
    if (settings.autostart_supported) body.append(option('Start on login', 'Open Syllune when you sign in.', 'autostart'));
    const retention = el('select', { class: 'select', 'aria-label': 'Keep history' });
    const choices = [[null, 'Until I delete it'], [30, '30 days'], [7, '7 days'], [0, 'Off']];
    if (!choices.some(([value]) => value === settings.retention_days)) choices.push([settings.retention_days, `${settings.retention_days} days`]);
    choices.forEach(([value, text], index) => retention.append(el('option', { value: String(index), text })));
    retention.value = String(choices.findIndex(([value]) => value === settings.retention_days));
    retention.addEventListener('change', () => run(async () => {
      const value = choices[Number(retention.value)][0];
      try { await api('setting_set', { key: 'retention_days', value }); settings.retention_days = value; }
      finally { retention.value = String(choices.findIndex(([item]) => item === settings.retention_days)); }
    }));
    body.append(label('Keep dictation history', retention,
      'History Off stops saving new dictations. Existing history and retained recordings can be deleted from History.'));
  }

  function tryIt() {
    finishHint = el('p', { class: 'setup-note', role: 'status' });
    const input = el('textarea', { class: 'textarea setup-scratch', rows: 5,
      'aria-label': 'Try dictation here', placeholder: 'Click here, hold your recording hotkey, and speak when you are ready.' });
    input.value = scratch;
    input.addEventListener('input', () => { scratch = input.value; });
    body.append(finishHint,
      paragraph(`Click the field below and try ${current.hotkeys.record}. This scratch field stays inside Syllune and is cleared when you leave the guide. A normal dictation still follows the audio and history choices you just selected.`),
      label('Your practice space', input),
      paragraph('The practice dictation is optional. Finish when your speech model is ready; you can reopen this guide from Settings.'));
  }

  // Background polling never reconstructs controls or discards unsaved text.
  function paintStatus() {
    if (gone || !current || !next) return;
    const transfer = current.download;
    const active = ['downloading', 'loading'].includes(transfer.state);
    back.disabled = pending || step === 0;
    later.disabled = pending;
    next.disabled = pending || (step === 5 && !current.ready);
    if (modelButton) {
      const model = current.models.find(item => item.key === current.model_key);
      modelButton.disabled = pending || active || current.ready;
      modelButton.textContent = active ? 'Preparing model…' : current.ready ? 'Model ready' : transfer.state === 'error' ? 'Retry selected model' : model.cached ? 'Use cached model' : 'Download selected model';
      if (transfer.total > 0) modelProgress.value = Math.min(100, 100 * transfer.completed / transfer.total);
      else modelProgress.removeAttribute('value');
      modelProgress.hidden = !active;
      const space = current.free_bytes === null ? 'Disk space unavailable.' : `${bytes(current.free_bytes)} free.`;
      modelMessage.textContent = transfer.state === 'error' ? transfer.error : current.ready ? 'Speech model loaded and ready for dictation.' : active
        ? transfer.state === 'loading' ? 'Loading the speech model…' : transfer.total > 0
          ? `Downloading: ${Math.round(100 * transfer.completed / transfer.total)}%` : 'Connecting and checking downloaded files…'
        : `${space} Allow about ${bytes(model.required_bytes)} for download and temporary files.`;
      for (const input of body.querySelectorAll('input[name="setup-model"]')) input.disabled = pending || active;
    }
    if (micMeter) {
      const mic = current.mic_test;
      micMeter.value = mic.level;
      micButton.disabled = pending;
      micButton.textContent = mic.active ? 'Stop microphone test' : mic.error ? 'Retry microphone test' : 'Start microphone test';
      micMessage.textContent = mic.error || (mic.active ? 'Checking input level. Stops after 15 seconds.' : mic.heard ? 'Input detected. No audio was saved.' : mic.tested ? 'No input detected. Check your selected microphone and its input volume, then try again.' : 'Test optional. Start, speak briefly, then Stop.');
    }
    if (finishHint) {
      const connected = current.mics.some(item => String(item.id) === String(current.mic));
      finishHint.textContent = `${current.ready ? '✓ Speech model ready.' : 'Speech model needed: return to model setup.'} ${connected ? '✓ Microphone connected.' : 'Select a connected microphone when you are ready to dictate.'} ${current.languages.length ? `Languages: ${current.languages.join(', ')}.` : 'Language detection: automatic.'}`;
    }
  }

  async function poll() {
    if (gone || polling) return;
    if (pending) return;
    polling = true;
    const requestRevision = revision;
    try {
      const status = await api('setup_status');
      if (!gone && !pending && requestRevision === revision) { current = status; paintStatus(); }
    }
    catch (error) { if (!gone) notice.textContent = `Connection interrupted. Reconnecting… ${error.message || error}`; }
    finally { polling = false; }
  }

  async function mount() {
    gone = false;
    pending = false;
    scratch = '';
    [current, settings, styles] = await Promise.all([api('setup_status'), api('settings'), api('styles')]);
    step = current.completed ? 0 : current.step;
    heading = el('h2', { class: 'setup-heading', tabIndex: -1 });
    notice = el('p', { class: 'setup-error', role: 'alert' });
    body = el('div', { class: 'setup-body' });
    back = button('Back', () => run(() => move(step - 1)));
    next = button('Continue', () => run(async () => {
      if (step < 5) return move(step + 1);
      await api('setup_complete');
      await Hub.navigate('home');
    }), true);
    later = button('Do this later', () => run(async () => {
      if (step === 5) return move(2);
      return move(step + 1);
    }));
    root = Hub.page('Set up Syllune', null, el('div', { class: 'setup-layout' },
      el('ol', { class: 'setup-steps', 'aria-label': 'Setup progress' }),
      el('section', { class: 'setup-content', 'aria-label': 'Setup step' }, heading, body, notice,
        el('div', { class: 'setup-navigation' }, back, later, next))));
    renderStep();
    return root;
  }

  return {
    title: 'Set up Syllune', mount,
    mounted: () => {
      clearInterval(timer);
      timer = setInterval(poll, 700);
      heading.focus();
    },
    leave: () => {
      gone = true;
      clearInterval(timer);
      scratch = '';
      api('setup_mic_test', { action: 'stop' }).catch(() => {});
    },
  };
})();
