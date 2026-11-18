// pages/dictionary.js
// What Syllune should know about how you talk, in one list: the names and
// terms it listens for, and the spellings it corrects when it still mishears
// one. Words you say often join the list by themselves; removing one keeps it
// away. Every change applies to the next dictation; nothing needs a restart.

'use strict';

Hub.pages.dictionary = (() => {
  const { el, api } = Hub;

  let panel = null;

  async function mount() {
    panel = el('div', { class: 'panel' });
    paint(await api('dictionary'));
    return Hub.page('Dictionary', null,
      el('p', { class: 'page-sub', text: 'The names and terms Syllune listens for, so they come out spelled your way.' }),
      panel);
  }

  // Every write returns the whole dictionary, so the panel is redrawn from
  // what the engine now has.
  async function change(method, params) {
    try {
      paint(await api(method, params));
      return true;
    } catch (error) {
      Hub.toastError(error);
      return false;
    }
  }

  function paint(data) {
    const words = data.words.length
      ? el('div', { class: 'chips' }, data.words.map(wordChip))
      : el('div', { class: 'faint', text: data.auto_learn ? 'Nothing yet. Names you say often will show up here.' : 'No words yet.' });

    panel.replaceChildren(
      el('h2', { class: 'section-title', text: 'Words' }),
      words,
      data.replacements.length ? fixes(data.replacements) : null,
      addRow(),
      el('div', { class: 'row dictionary-auto' },
        el('div', { class: 'row-text' },
          el('div', { class: 'row-label', text: 'Add words I say often' }),
          el('div', { class: 'row-hint', text: 'Names and terms that keep coming up in your dictations are added by themselves.' })),
        el('div', { class: 'row-control' }, Hub.toggle({
          checked: data.auto_learn, label: 'Add words I say often',
          onChange: async (enabled) => paint(await api('dictionary_auto', { enabled })),
        }))));
  }

  // A word Syllune added itself looks a little quieter than one you typed.
  function wordChip(item) {
    const chip = el('span', { class: item.learned ? 'chip learned' : 'chip', title: item.learned ? 'Added because you say it often' : null }, item.word);
    chip.append(el('button', {
      class: 'chip-remove', type: 'button', 'aria-label': `Remove ${item.word}`,
      on: {
        click: async () => {
          chip.classList.add('leaving');
          if (!await change('dictionary_remove', { word: item.word })) chip.classList.remove('leaving');
        },
      },
    }, Hub.icon('close')));
    return chip;
  }

  // Spellings that are always corrected. Fixing a dictation in History adds
  // these too.
  function fixes(replacements) {
    return el('div', { class: 'fixes' },
      el('div', { class: 'field-label', text: 'Always corrected' }),
      el('div', { class: 'replacements' }, replacements.map((item) => {
        const row = el('div', { class: 'replacement' },
          el('span', { class: 'heard', text: item.from }),
          el('span', { class: 'arrow', text: '→' }),
          el('span', { class: 'fixed', text: item.to }));
        row.append(Hub.iconButton('close', `Remove ${item.from}`, async () => {
          row.classList.add('leaving');
          if (!await change('replacement_remove', { heard: item.from })) row.classList.remove('leaving');
        }));
        return row;
      })));
  }

  // One way to add: the word, and, only if Syllune keeps writing it wrong,
  // what it writes instead.
  function addRow() {
    const word = el('input', { class: 'input', type: 'text', spellcheck: 'false', placeholder: 'Word or name', 'aria-label': 'Word or name', maxLength: 80 });
    const heard = el('input', { class: 'input', type: 'text', spellcheck: 'false', placeholder: 'Gets written as (optional)', 'aria-label': 'Gets written as', maxLength: 80 });
    const add = async () => {
      if (!word.value.trim()) { word.focus(); return; }
      if (await change('dictionary_add', { word: word.value.trim(), heard: heard.value.trim() })) {
        panel.querySelector('.word-add .input')?.focus();
      }
    };
    for (const input of [word, heard]) input.addEventListener('keydown', (event) => { if (event.key === 'Enter') add(); });
    return el('div', { class: 'word-add' }, word, heard,
      el('button', { class: 'button', type: 'button', text: 'Add', on: { click: add } }));
  }

  // Words learned while the page is open appear without a reload.
  async function onEvent(name) {
    if (name !== 'dictionary' || !panel || panel.contains(document.activeElement)) return;
    try { paint(await api('dictionary')); } catch { /* the next visit shows them */ }
  }

  return { title: 'Dictionary', mount, onEvent };
})();
