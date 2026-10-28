// pages/history.js
// Every saved dictation, newest first, grouped by day and searchable. Fixing
// a dictation here also teaches the fix: hub_api works out which words were
// misheard and adds them as replacements, and the toast offers to take that
// back. Delete asks for a second click instead of a dialog.

'use strict';

Hub.pages.history = (() => {
  const { el, api } = Hub;
  const PAGE_SIZE = 100;
  const CONFIRM_MS = 3000;

  let query = '';
  let shown = PAGE_SIZE;
  let data = { items: [], total: 0, saving: true };
  let list = null;
  let search = null;
  let editing = null;              // {row, item} while an entry is being edited
  let missedUpdate = false;        // a dictation arrived during an edit

  async function mount() {
    query = '';
    shown = PAGE_SIZE;
    editing = null;
    missedUpdate = false;
    await load();
    search = el('input', {
      class: 'input search', type: 'text', placeholder: 'Search', 'aria-label': 'Search history',
      spellcheck: 'false',
      on: { input: Hub.debounce(onSearch, 150), keydown: onSearchKey },
    });
    list = el('div', { class: 'history' });
    render();
    return Hub.page('History', [search], list);
  }

  async function load() {
    data = await api('history', { query, limit: shown, offset: 0 });
  }

  async function onSearch() {
    query = search.value.trim();
    shown = PAGE_SIZE;
    try {
      await load();
      render();
    } catch (error) {
      Hub.toastError(error);
    }
  }

  function onSearchKey(event) {
    if (event.key === 'Escape' && search.value) {
      search.value = '';
      onSearch();
    }
  }

  // --- Rendering -----------------------------------------------------------

  function render() {
    list.replaceChildren();
    if (!data.items.length) {
      list.append(emptyState());
      return;
    }
    if (!data.saving) {
      list.append(el('p', { class: 'page-sub', text: "History is off, so new dictations aren't being saved." }));
    }
    let group = null;
    let groupDay = null;
    for (const item of data.items) {
      const date = Hub.parseTimestamp(item.timestamp);
      const day = date.toDateString();
      if (day !== groupDay) {
        groupDay = day;
        group = el('section', { class: 'day' }, el('h2', { class: 'day-title', text: Hub.formatDayHeading(date) }));
        list.append(group);
      }
      group.append(entryRow(item, date));
    }
    if (data.items.length < data.total) {
      list.append(el('div', { class: 'more' },
        el('button', { class: 'button', type: 'button', text: 'Show more', on: { click: showMore } })));
    }
  }

  function emptyState() {
    if (query) {
      return el('div', { class: 'empty' }, el('div', { class: 'empty-title', text: `Nothing matches “${query}”` }));
    }
    if (!data.saving) {
      return el('div', { class: 'empty' },
        el('div', { class: 'empty-title', text: 'History is off' }),
        el('div', { text: "New dictations aren't saved. You can turn this on in Settings." }),
        el('div', { style: { 'margin-top': '16px' } },
          el('button', { class: 'button', type: 'button', text: 'Open Settings', on: { click: () => Hub.navigate('settings') } })));
    }
    return el('div', { class: 'empty' },
      el('div', { class: 'empty-title', text: 'No dictations yet' }),
      el('div', {}, 'Hold ', el('kbd', { text: 'Ctrl' }), ' ', el('kbd', { text: 'Win' }), ' and talk. What you say shows up here.'));
  }

  function entryRow(item, date) {
    const row = el('div', { class: 'entry' });
    row.append(
      el('div', { class: 'entry-time', text: Hub.formatTime(date) }),
      entryBody(item),
      el('div', { class: 'entry-actions' },
        Hub.iconButton('copy', 'Copy', () => Hub.copy(item.text)),
        Hub.iconButton('edit', 'Edit', () => startEdit(row, item)),
        deleteButton(row, item)));
    return row;
  }

  function entryBody(item) {
    return el('div', { class: 'entry-body' },
      el('div', { class: 'entry-text', text: item.text }),
      item.delivery && item.delivery !== 'inserted' ? el('span', { class: 'entry-delivery', text: ({pending: 'Waiting to insert', delivering: 'Check insertion', suppressed: 'Saved without insertion', failed: 'Saved without insertion'})[item.delivery] || '' }) : null,
      entryMeta(item));
  }

  // The app name and the less frequent actions share one line, so revealing
  // the actions on hover never changes a row's height. Rows saved before
  // entries had IDs keep only the app name.
  function entryMeta(item) {
    const links = item.id ? [
      entryLink('Structure', 'Preview this as a structured prompt', (button) => structure(item, button)),
      entryLink('Queue', 'Insert into the next text field you click', () => queue(item)),
    ].filter(Boolean) : [];
    const listenButton = item.id && item.has_audio ? el('button', {
      class: 'entry-listen', type: 'button', title: 'Play the recording', 'aria-expanded': 'false',
      on: { click: (event) => listen(event.currentTarget.closest('.entry'), item, event.currentTarget) } },
      Hub.icon('play'), el('span', { text: 'Listen' })) : null;
    if (!item.app && !links.length && !listenButton) return null;
    return el('div', { class: 'entry-meta' },
      listenButton,
      item.app ? Hub.appBadge(item.app) : null,
      links.length ? el('span', { class: 'entry-links' }, ...links) : null);
  }

  function entryLink(text, title, action) {
    return el('button', { class: 'entry-link', type: 'button', text, title,
      on: { click: (event) => action(event.currentTarget) } });
  }

  async function queue(item, text) {
    try {
      await api('history_queue', { entry_id: item.id, ...(text === undefined ? {} : {text}) });
      Hub.toast('Queued. Click a text field to insert the next dictation.');
      return true;
    } catch (error) { Hub.toastError(error); return false; }
  }

  // The recording plays in a player under the text, the same one meetings
  // use. A second click hides it again, and starting one recording pauses
  // any other, so two dictations never talk over each other.
  async function listen(row, item, button) {
    try {
      let audio = row.querySelector('audio');
      if (audio) {
        audio.pause();
        audio.remove();
        button.setAttribute('aria-expanded', 'false');
        button.lastChild.textContent = 'Listen';
        return;
      }
      const src = await window.hub.audioSource('dictation', item.id, 'playback');
      audio = el('audio', { class: 'meeting-audio entry-audio', controls: true, preload: 'auto', src,
        'aria-label': 'Dictation recording',
        on: { play: (event) => pauseOtherPlayers(event.currentTarget),
              error: () => Hub.toast('This recording could not be opened.') } });
      row.querySelector('.entry-body').append(audio);
      button.setAttribute('aria-expanded', 'true');
      button.lastChild.textContent = 'Hide';
      await audio.play();
    } catch (error) { Hub.toastError(error); }
  }

  function pauseOtherPlayers(current) {
    for (const other of document.querySelectorAll('audio')) {
      if (other !== current) other.pause();
    }
  }

  // Structure never changes the original row or text in another application.
  async function structure(item, button) {
    button.disabled = true;
    try {
      const result = await api('history_structure', {entry_id: item.id});
      const preview = el('textarea', {class: 'textarea', rows: 10, 'aria-label': 'Structured prompt preview'});
      preview.value = result.text;
      const dialog = el('dialog', {class: 'structure-dialog', 'aria-labelledby': 'structure-title'},
        el('h2', {id: 'structure-title', text: 'Structure preview'}),
        el('p', {class: 'muted', text: 'Review the result. Your original text and recording stay saved.'}), preview,
        el('details', {class: 'selectable'}, el('summary', {text: 'Original words'}),
          el('p', {text: item.original_text || result.original})),
        el('div', {class: 'page-actions'},
          el('button', {class: 'button', type: 'button', text: 'Close', on: {click: () => dialog.close()}}),
          el('button', {class: 'button', type: 'button', text: 'Copy', on: {click: () => Hub.copy(preview.value)}}),
          el('button', {class: 'button primary', type: 'button', text: 'Insert', on: {click: async () => {
            if (await queue(item, preview.value)) dialog.close();
          }}})));
      dialog.addEventListener('close', () => dialog.remove());
      document.body.append(dialog); dialog.showModal(); preview.focus();
    } catch (error) { Hub.toastError(error); }
    finally { button.disabled = false; }
  }

  async function showMore() {
    shown += PAGE_SIZE;
    try {
      await load();
      render();
    } catch (error) {
      Hub.toastError(error);
    }
  }

  // --- Editing -------------------------------------------------------------

  function startEdit(row, item) {
    if (editing) stopEdit();
    editing = { row, item };
    row.classList.add('editing');
    const textarea = el('textarea', { class: 'textarea', spellcheck: 'true', 'aria-label': 'Edit dictation' });
    textarea.value = item.text;
    const grow = () => {
      textarea.style.height = 'auto';
      textarea.style.height = `${textarea.scrollHeight + 2}px`;
    };
    textarea.addEventListener('input', grow);
    textarea.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') { event.preventDefault(); stopEdit(); }
      if (event.key === 'Enter' && event.ctrlKey) { event.preventDefault(); save(textarea.value); }
    });
    const editor = el('div', { class: 'entry-editor' },
      textarea,
      el('div', { class: 'entry-editor-buttons' },
        el('span', { class: 'hint', text: 'Fixed words are learned for next time. Ctrl Enter saves.' }),
        el('button', { class: 'button quiet', type: 'button', text: 'Cancel', on: { click: stopEdit } }),
        el('button', { class: 'button primary', type: 'button', text: 'Save', on: { click: () => save(textarea.value) } })));
    row.querySelector('.entry-body').replaceWith(editor);
    grow();
    textarea.focus();
    textarea.setSelectionRange(textarea.value.length, textarea.value.length);
  }

  function stopEdit() {
    if (!editing) return;
    const { row, item } = editing;
    editing = null;
    row.classList.remove('editing');
    row.querySelector('.entry-editor')?.replaceWith(entryBody(item));
    if (missedUpdate) {
      missedUpdate = false;
      refresh();
    }
  }

  async function save(text) {
    if (!editing) return;
    const { item } = editing;
    const edited = text.trim();
    if (!edited || edited === item.text) {
      stopEdit();
      return;
    }
    try {
      const result = await api('history_update', { timestamp: item.timestamp, original: item.text, edited });
      item.text = edited;
      stopEdit();
      announceLearned(result.learned || []);
    } catch (error) {
      Hub.toastError(error);
    }
  }

  // "Learned: cloud code → Claude Code", with a way to keep the edit but not
  // the learning.
  function announceLearned(learned) {
    if (!learned.length) {
      Hub.toast('Saved');
      return;
    }
    const first = `${learned[0].from} → ${learned[0].to}`;
    const more = learned.length > 1 ? ` and ${learned.length - 1} more` : '';
    Hub.toast(`Learned ${first}${more}`, {
      label: "Don't learn",
      run: async () => {
        for (const fix of learned) await api('replacement_remove', { heard: fix.from });
        Hub.toast('Kept the edit, forgot the fix');
      },
    });
  }

  // --- Deleting ------------------------------------------------------------

  // First click arms it, second click deletes; it disarms on its own.
  function deleteButton(row, item) {
    const button = Hub.iconButton('trash', 'Delete', () => {
      if (!button.classList.contains('confirm')) {
        button.classList.add('confirm');
        button.replaceChildren('Delete');
        timer = setTimeout(disarm, CONFIRM_MS);
        return;
      }
      clearTimeout(timer);
      remove(row, item);
    });
    let timer = null;
    const disarm = () => {
      button.classList.remove('confirm');
      button.replaceChildren(Hub.icon('trash'));
    };
    button.addEventListener('blur', () => { clearTimeout(timer); disarm(); });
    return button;
  }

  async function remove(row, item) {
    try {
      await api('history_delete', { timestamp: item.timestamp, text: item.text });
    } catch (error) {
      Hub.toastError(error);
      return;
    }
    row.classList.add('leaving');
    setTimeout(() => {
      data.items = data.items.filter((other) => other !== item);
      data.total = Math.max(0, data.total - 1);
      render();
    }, 170);
  }

  // --- Live updates --------------------------------------------------------

  const refresh = Hub.debounce(async () => {
    if (editing) { missedUpdate = true; return; }
    try {
      await load();
      render();
    } catch {
      // The next dictation tries again.
    }
  }, 300);

  return {
    title: 'History',
    mount,
    leave: () => { editing = null; document.querySelector('.structure-dialog')?.remove(); },
    focusSearch: () => search?.focus(),
    onEvent: (name) => { if (name === 'transcript' && !query) refresh(); },
  };
})();
