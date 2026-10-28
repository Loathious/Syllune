// pages/notes.js
// Voice notes: the markdown files in the notes folder, as a searchable list
// beside a plain editor. Edits save themselves after a short pause; delete
// moves the file to the Recycle Bin, so a misclick is recoverable.

'use strict';

Hub.pages.notes = (() => {
  const { el } = Hub;
  const SAVE_DELAY_MS = 600;

  let notes = [];
  let current = null;
  let saveTimer = null;
  let parts = null;                // the page's live nodes

  async function mount() {
    notes = await window.hub.notes.list();
    current = notes[0] || null;
    parts = {
      search: el('input', {
        class: 'input search', type: 'text', placeholder: 'Search notes', 'aria-label': 'Search notes',
        spellcheck: 'false', on: { input: renderList },
      }),
      list: el('div', { class: 'notes-list', role: 'list' }),
      meta: el('span', { class: 'meta' }),
      editor: el('textarea', { class: 'notes-editor', spellcheck: 'true', 'aria-label': 'Note' }),
    };
    parts.editor.addEventListener('input', onType);
    parts.bar = el('div', { class: 'notes-bar' },
      parts.meta,
      el('button', { class: 'button quiet', type: 'button', text: 'Copy', on: { click: copyNote } }),
      el('button', { class: 'button quiet danger', type: 'button', text: 'Delete', on: { click: trashNote } }));
    parts.empty = el('div', { class: 'notes-empty' },
      el('div', { class: 'empty-title', text: 'No notes yet' }),
      el('div', {}, 'Hold ', el('kbd', { text: 'Ctrl' }), ' ', el('kbd', { text: 'Shift' }), ' ', el('kbd', { text: 'Alt' }),
        ' and talk, or double-tap it for hands-free.'));
    parts.pane = el('div', { class: 'notes-editor-pane' });

    const page = Hub.page('Notes', [
      el('button', { class: 'button', type: 'button', on: { click: () => window.hub.notes.reveal() } }, Hub.icon('folder'), 'Open folder'),
    ], el('div', { class: 'notes' },
      el('div', { class: 'notes-list-pane' }, el('div', { class: 'notes-search' }, parts.search), parts.list),
      parts.pane));
    showCurrent();
    renderList();
    return page;
  }

  // --- Helpers -------------------------------------------------------------

  function titleOf(note) {
    const first = note.text.split('\n').find((line) => line.trim()) || note.name;
    return first.replace(/^#+\s*/, '').trim() || 'Untitled';
  }

  // Notes are named "YYYY-MM-DD HHMM ...", which reads better than mtime.
  function dateOf(note) {
    const match = note.name.match(/^(\d{4})-(\d{2})-(\d{2}) (\d{2})(\d{2})/);
    const date = match
      ? new Date(+match[1], +match[2] - 1, +match[3], +match[4], +match[5])
      : new Date(note.mtime);
    return `${Hub.formatDayHeading(date)}, ${Hub.formatTime(date)}`;
  }

  // --- Rendering -----------------------------------------------------------

  function renderList() {
    const q = parts.search.value.trim().toLowerCase();
    const shown = q ? notes.filter((note) => note.text.toLowerCase().includes(q)) : notes;
    parts.list.replaceChildren(...shown.map((note) => el('button', {
      class: 'note-item', type: 'button', role: 'listitem',
      'aria-current': note === current ? 'true' : 'false',
      on: { click: () => select(note) },
    },
      el('div', { class: 'note-title', text: titleOf(note) }),
      el('div', { class: 'note-date', text: dateOf(note) }))));
    if (!shown.length && notes.length) parts.list.append(el('div', { class: 'faint', style: { padding: '8px 10px' }, text: 'No matches' }));
  }

  function showCurrent() {
    if (!current) {
      parts.pane.replaceChildren(parts.empty);
      return;
    }
    parts.editor.value = current.text;
    parts.meta.textContent = dateOf(current);
    parts.pane.replaceChildren(parts.bar, parts.editor);
  }

  function select(note) {
    if (note === current) return;
    flush();
    current = note;
    showCurrent();
    renderList();
  }

  // --- Editing -------------------------------------------------------------

  function onType() {
    if (!current) return;
    current.text = parts.editor.value;
    clearTimeout(saveTimer);
    saveTimer = setTimeout(flush, SAVE_DELAY_MS);
    renderTitleOnly();
  }

  // The list title follows the first line as you type, without rebuilding
  // the list (which would lose its scroll position).
  function renderTitleOnly() {
    const index = [...parts.list.children].findIndex((node) => node.getAttribute('aria-current') === 'true');
    const title = parts.list.children[index]?.querySelector('.note-title');
    if (title) title.textContent = titleOf(current);
  }

  function flush() {
    if (!saveTimer || !current) return;
    clearTimeout(saveTimer);
    saveTimer = null;
    window.hub.notes.save(current.name, current.text).catch(Hub.toastError);
  }

  // Copies the body without the generated "# title" line.
  function copyNote() {
    if (current) Hub.copy(current.text.replace(/^#[^\n]*\n+/, '').trim());
  }

  async function trashNote() {
    if (!current) return;
    clearTimeout(saveTimer);
    saveTimer = null;
    try {
      await window.hub.notes.trash(current.name);
    } catch (error) {
      Hub.toastError(error);
      return;
    }
    const index = notes.indexOf(current);
    notes.splice(index, 1);
    current = notes[Math.min(index, notes.length - 1)] || null;
    showCurrent();
    renderList();
    Hub.toast('Moved to the Recycle Bin');
  }

  // --- Live updates --------------------------------------------------------

  // A new note arrived. Open it, unless you're in the middle of typing.
  async function reloadNotes() {
    const typing = saveTimer || document.activeElement === parts.editor;
    flush();
    const keep = current?.name;
    notes = await window.hub.notes.list();
    current = (typing ? notes.find((note) => note.name === keep) : notes[0]) || notes[0] || null;
    if (!typing) showCurrent();
    renderList();
  }

  return {
    title: 'Notes',
    fill: true,
    mount,
    leave: flush,
    focusSearch: () => parts?.search.focus(),
    onEvent: (name) => { if (name === 'notes') reloadNotes().catch(Hub.toastError); },
  };
})();
