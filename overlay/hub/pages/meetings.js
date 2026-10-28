// Meetings: record this PC's microphone and call audio, then read local notes.
// Engine events refresh progress; the clock runs only while this page is open.
// Speaker names and transcript text use textContent, including imported text.
'use strict';

Hub.pages.meetings = (() => {
  const { el, api } = Hub;
  let root, body, record, clock, timer, currentId = null;
  let data = {}, active = false, revision = 0, busy = false;
  let detailSignature = '', notices = null, player = null;
  let selectedSpeaker = '', searchText = '';
  let deferredRefresh = false;
  const STATUS = { recording: 'Recording', waiting: 'Waiting', processing: 'Processing', ready: 'Ready', failed: 'Needs attention' };

  function elapsed(seconds) {
    const s = Math.max(0, Math.floor(seconds || 0));
    const m = Math.floor(s / 60);
    return m >= 60 ? `${Math.floor(m / 60)}:${String(m % 60).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`
      : `${m}:${String(s % 60).padStart(2, '0')}`;
  }

  async function mount() {
    active = true;
    clock = el('span', { class: 'meta tabular', 'aria-label': 'Recording duration' });
    record = el('button', { class: 'button primary', type: 'button', on: { click: toggleRecording } });
    body = el('div');
    body.addEventListener('focusout', () => {
      if (!deferredRefresh) return;
      requestAnimationFrame(() => {
        if (!active || !deferredRefresh || (body.contains(document.activeElement) &&
            ['INPUT', 'SELECT'].includes(document.activeElement.tagName))) return;
        deferredRefresh = false;
        refresh().catch(Hub.toastError);
      });
    });
    root = Hub.page('Meetings', [clock, record],
      el('p', { class: 'page-sub', text: 'Record your microphone and this PC’s call audio. Transcripts and notes stay on this PC.' }), body);
    await refresh();
    return root;
  }

  function tick() {
    clock.textContent = data.recording_since ? elapsed((Date.now() - data.recording_since) / 1000) : '';
  }

  // Events can arrive during a write or navigation; only the newest read wins.
  async function refresh() {
    const ticket = ++revision;
    const next = await api('meetings');
    const meeting = currentId ? await api('meeting', { meeting_id: currentId }).catch(() => null) : null;
    if (!active || ticket !== revision) return;
    data = next;
    if (currentId && !meeting) currentId = null;
    clearInterval(timer);
    tick();
    if (data.recording_since) timer = setInterval(tick, 1000);
    record.textContent = data.recording_since ? 'Stop recording' : 'Record a meeting';
    record.classList.toggle('danger', !!data.recording_since);
    record.classList.toggle('primary', !data.recording_since);
    record.disabled = busy;
    // A progress update must not steal focus or erase a speaker name being typed.
    if (meeting && notices) updateNotices(meeting);
    if (body.contains(document.activeElement) && ['INPUT', 'SELECT'].includes(document.activeElement.tagName)) {
      deferredRefresh = true;
      return;
    }
    deferredRefresh = false;
    if (meeting) {
      // Progress and other meetings' events must not reset playback or filters.
      const { step, progress, error, capture_warnings, ...content } = meeting;
      const signature = JSON.stringify(content);
      if (signature !== detailSignature) { paintDetail(meeting); detailSignature = signature; }
    } else { detailSignature = ''; notices = null; player?.pause(); player = null; paintList(); }
  }

  async function toggleRecording() {
    if (busy) return;
    busy = true; record.disabled = true;
    const stopping = !!data.recording_since;
    try {
      data = await api(stopping ? 'meeting_stop' : 'meeting_start');
      if (stopping) Hub.toast('Recording stopped. Audio stays until you delete the meeting.');
      await refresh();
    } catch (error) { Hub.toastError(error); }
    finally { busy = false; record.disabled = false; }
  }

  function status(meeting) {
    return el('span', { class: `meeting-status ${meeting.status}` }, STATUS[meeting.status] || meeting.status);
  }

  function paintList() {
    const meetings = data.meetings || [];
    if (!meetings.length) {
      body.replaceChildren(el('div', { class: 'empty' },
        el('div', { class: 'empty-title', text: 'Keep the conversation, skip the note-taking' }),
        el('p', { class: 'muted', text: 'Start recording before a call. When you stop, Whisper separates speakers and writes a transcript, summary and action items.' }),
        el('p', { class: 'faint', text: 'Processing waits while you game. Audio stays until you delete the meeting.' })));
      return;
    }
    body.replaceChildren(el('div', { class: 'meeting-list' }, meetings.map((m) =>
      el('button', { class: 'panel meeting-card', type: 'button', on: { click: async () => {
        currentId = m.id;
        selectedSpeaker = ''; searchText = ''; detailSignature = '';
        await refresh().catch(Hub.toastError);
        body.querySelector('.meeting-back')?.focus();
      } } },
      el('div', { class: 'meeting-card-head' }, el('span', { class: 'meeting-title', text: m.title || 'Meeting' }), status(m)),
      el('div', { class: 'meta', text: `${Hub.formatWhen(new Date(m.started))} · ${elapsed(m.duration)}` }),
      el('p', { class: 'meeting-preview', text: m.step || m.error || m.preview || 'No speech captured.' })))));
  }

  // Keep a failed action in context; every mutation re-reads the stored result.
  function action(label, method, id, props = {}) {
    return el('button', { class: 'button', type: 'button', text: label, ...props, on: { click: async (event) => {
      const button = event.currentTarget;
      button.disabled = true;
      try {
        const result = await api(method, { meeting_id: id });
        if (result.markdown_kept) Hub.toast('Your edited Markdown file was kept.');
        await refresh();
      } catch (error) { Hub.toastError(error); }
      finally { button.disabled = false; }
    } } });
  }

  function paintDetail(m) {
    player?.pause(); player = null;
    const back = el('button', { class: 'button quiet meeting-back', type: 'button', text: '← All meetings', on: { click: () => {
      currentId = null; refresh().catch(Hub.toastError);
    } } });
    const header = el('div', { class: 'meeting-detail-head' }, back, status(m));
    const sections = [header,
      el('h2', { class: 'meeting-detail-title selectable', text: m.title || 'Meeting' }),
      el('p', { class: 'meta', text: `${Hub.formatWhen(new Date(m.started))} · ${elapsed(m.duration)}${m.source === 'call' ? ' · Call' : m.source === 'room' ? ' · In the room' : ''}` })];
    const ready = m.status === 'ready';
    notices = el('div', { class: 'meeting-notices' });
    sections.push(notices); updateNotices(m);
    const actions = [];
    if (m.markdown_exists) actions.push(action('Open file', 'meeting_open', m.id));
    if (ready && m.turns?.length) actions.push(action('Summarise again', 'meeting_summarize', m.id, { disabled: !data.ai_enabled }));
    if (m.can_retry) actions.push(action('Retry processing', 'meeting_retry', m.id));
    if (m.status === 'waiting') actions.push(action('Process now', 'meeting_process_now', m.id));
    if (!['recording', 'processing', 'waiting'].includes(m.status)) actions.push(deleteButton(m.id));
    if (actions.length) sections.push(el('div', { class: 'page-actions meeting-actions' }, actions));
    if (m.audio_retained && m.status !== 'recording') sections.push(audioPanel(m));
    if (Object.keys(m.speakers || {}).length) sections.push(el('section', { class: 'panel' },
      el('h3', { class: 'section-title', text: 'Speakers' }),
      el('p', { class: 'section-hint', text: 'Name each voice to update the transcript and notes.' }),
      el('div', { class: 'meeting-speakers' }, Object.entries(m.speakers).map(([key, name]) => speaker(m, key, name, ready)))));
    const summary = m.summary || {};
    if (summary.summary) sections.push(el('section', { class: 'panel selectable' },
      el('h3', { class: 'section-title', text: 'Summary' }), el('p', { text: summary.summary })));
    for (const [key, title] of [['points', 'Key points'], ['decisions', 'Decisions'], ['actions', 'Action items']]) {
      if (summary[key]?.length) sections.push(el('section', { class: 'panel selectable' },
        el('h3', { class: 'section-title', text: title }),
        el('ul', { class: 'meeting-points' }, summary[key].map((text) => el('li', { text })))));
    }
    if (m.turns?.length) sections.push(transcriptPanel(m));
    else if (ready) sections.push(el('p', { class: 'muted', text: 'No speech was detected in this recording.' }));
    body.replaceChildren(...sections);
  }

  // Only this small region changes during an otherwise unchanged detail view.
  function updateNotices(m) {
    const children = [];
    if (m.step) {
      const progress = el('div', { class: 'panel meeting-progress', role: 'status', 'aria-atomic': 'true' }, m.step);
      if (typeof m.progress === 'number') progress.append(el('progress', { max: '1', value: String(m.progress), 'aria-label': 'Meeting processing progress' }));
      children.push(progress);
    }
    if (m.error) children.push(el('p', { class: 'error-line', role: 'status', text: m.error }));
    for (const warning of m.capture_warnings || []) children.push(el('p', { class: 'meeting-warning', role: 'status', text: warning }));
    notices.replaceChildren(...children);
  }

  function audioPanel(m) {
    player = el('audio', { class: 'meeting-audio', controls: true, preload: 'metadata', 'aria-label': 'Retained meeting audio' });
    const audio = player;
    const tracks = Object.entries(m.audio_tracks || {});
    const selection = el('select', { class: 'select', 'aria-label': 'Audio track' }, tracks.map(([key, track]) => el('option', { value: key, text: track.label })));
    const feedback = el('p', { class: 'section-hint', text: 'Audio is stored on this PC until you delete this meeting. Choose a timestamp below to listen from that point.' });
    let sourceRevision = 0;
    const loadTrack = async () => {
      const ticket = ++sourceRevision;
      const position = Number.isFinite(audio.currentTime) ? audio.currentTime : 0;
      const resume = !audio.paused;
      audio.pause();
      try {
        const source = await window.hub.audioSource('meeting', m.id, selection.value);
        if (!active || currentId !== m.id || ticket !== sourceRevision || player !== audio) return;
        audio.src = source;
        audio.addEventListener('loadedmetadata', () => {
          if (ticket !== sourceRevision || player !== audio) return;
          audio.currentTime = Math.min(position, Math.max(0, audio.duration || 0));
          if (resume) audio.play().catch(Hub.toastError);
        }, { once: true });
      } catch (error) { if (ticket === sourceRevision) Hub.toastError(error); }
    };
    selection.addEventListener('change', loadTrack);
    audio.addEventListener('error', () => { feedback.textContent = 'This audio could not be opened. Try another retained track.'; });
    if (tracks.length) loadTrack();
    return el('section', { class: 'panel meeting-playback' },
      el('div', { class: 'meeting-playback-head' }, el('h3', { class: 'section-title', text: 'Recording' }), selection), audio, feedback);
  }

  function transcriptPanel(m) {
    if (selectedSpeaker && !m.speakers[selectedSpeaker]) selectedSpeaker = '';
    const speakerSelect = el('select', { class: 'select', 'aria-label': 'Transcript speaker' },
      el('option', { value: '', text: 'All speakers' }),
      Object.entries(m.speakers).map(([id, name]) => el('option', { value: id, text: name })));
    speakerSelect.value = selectedSpeaker;
    const search = el('input', { class: 'input meeting-transcript-search', type: 'search', value: searchText,
      placeholder: 'Search transcript', 'aria-label': 'Search transcript' });
    const turns = el('div', { class: 'meeting-transcript-turns' });
    const count = el('p', { class: 'section-hint', role: 'status' });
    const matchingTurns = () => (m.turns || []).filter((turn) => (!selectedSpeaker || turn.speaker === selectedSpeaker) &&
      (!searchText.trim() || `${m.speakers[turn.speaker]} ${turn.text}`.toLocaleLowerCase().includes(searchText.trim().toLocaleLowerCase())));
    const paintTurns = () => {
      const visible = matchingTurns();
      count.textContent = `${visible.length} of ${m.turns.length} passages`;
      turns.replaceChildren(...visible.map((turn) => el('div', { class: 'meeting-turn' },
        el('div', { class: 'meeting-turn-head' }, el('strong', { text: m.speakers[turn.speaker] || turn.speaker }),
          el('button', { class: 'meeting-timestamp tabular', type: 'button', text: elapsed(turn.start),
            disabled: !player, 'aria-label': `Play from ${elapsed(turn.start)}`, on: { click: () => {
              if (!player) return;
              const audio = player;
              const seek = () => { audio.currentTime = turn.start; audio.play().catch(Hub.toastError); };
              if (audio.readyState >= 1) seek(); else audio.addEventListener('loadedmetadata', seek, { once: true });
            } } })), el('p', { text: turn.text }))));
      if (!visible.length) turns.append(el('p', { class: 'muted', text: 'No matching passages.' }));
    };
    speakerSelect.addEventListener('change', () => { selectedSpeaker = speakerSelect.value; paintTurns(); });
    search.addEventListener('input', () => { searchText = search.value; paintTurns(); });
    const copy = el('button', { class: 'button', type: 'button', text: 'Copy shown text', on: { click: async () => {
      const text = matchingTurns().map((turn) => `[${elapsed(turn.start)}] ${m.speakers[turn.speaker] || turn.speaker}\n${turn.text}`).join('\n\n');
      try { await window.hub.copy(text); Hub.toast('Transcript copied.'); } catch (error) { Hub.toastError(error); }
    } } });
    const open = el('button', { class: 'button', type: 'button', text: 'Open transcript file', on: { click: async () => {
      try {
        const result = await api('meeting_open_transcript', { meeting_id: m.id, speaker: selectedSpeaker });
        if (result.preserved) Hub.toast('Opened your edited transcript.');
      } catch (error) { Hub.toastError(error); }
    } } });
    paintTurns();
    return el('section', { class: 'panel selectable' }, el('h3', { class: 'section-title', text: 'Transcript' }),
      el('div', { class: 'meeting-transcript-tools' }, speakerSelect, search, copy, open), count, turns);
  }

  function speaker(m, key, name, ready) {
    const input = el('input', { class: 'input', value: name, maxLength: 40, disabled: !ready, 'aria-label': `Name for ${name}` });
    input.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') input.blur();
      if (event.key === 'Escape') { input.value = name; input.blur(); }
    });
    input.addEventListener('change', async () => {
      input.disabled = true;
      try {
        const result = await api('meeting_rename_speaker', { meeting_id: m.id, key, name: input.value });
        if (result.markdown_kept || result.transcript_kept) Hub.toast('Speaker renamed. Your edited files were kept.');
        await refresh();
      } catch (error) { input.value = name; Hub.toastError(error); }
      finally { input.disabled = !ready; }
    });
    return input;
  }

  function deleteButton(id) {
    let armed = false;
    return el('button', { class: 'button danger', type: 'button', text: 'Delete', on: { click: async (event) => {
      const button = event.currentTarget;
      if (!armed) {
        armed = true; button.textContent = 'Delete meeting?';
        Hub.toast('Deletes meeting audio and app data. Your Markdown notes and transcripts stay.');
        setTimeout(() => { armed = false; button.textContent = 'Delete'; }, 5000);
        return;
      }
      button.disabled = true;
      try { await api('meeting_delete', { meeting_id: id }); currentId = null; await refresh(); }
      catch (error) { button.disabled = false; Hub.toastError(error); }
    } } });
  }

  return {
    title: 'Meetings', mount,
    onEvent: (name) => { if (name === 'meetings') refresh().catch(Hub.toastError); },
    leave: () => { active = false; revision++; clearInterval(timer); currentId = null; detailSignature = ''; notices = null;
      player?.pause(); player = null; selectedSpeaker = ''; searchText = ''; deferredRefresh = false; },
  };
})();
