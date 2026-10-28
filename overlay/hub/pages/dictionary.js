// pages/dictionary.js
// What Whisper should know about how you talk: words to listen for (names,
// brands, jargon), replacements for words it keeps mishearing, and
// suggestions mined from your own history. Every change applies to the next
// dictation; nothing needs a restart.

'use strict';

Hub.pages.dictionary = (() => {
  const { el, api } = Hub;

  let sections = null;             // {words, replacements, suggestions} panels

  async function mount() {
    const data = await api('dictionary');
    sections = {
      words: el('div', { class: 'panel' }),
      replacements: el('div', { class: 'panel' }),
      suggestions: el('div', { class: 'panel' }),
    };
    paint(data);
    return Hub.page('Dictionary', null,
      el('p', { class: 'page-sub', text: 'Teach Whisper your names and terms, and fix words it keeps getting wrong.' }),
      sections.words, sections.replacements, sections.suggestions);
  }

  // Every write returns the whole dictionary, so each panel is redrawn from
  // what the engine now has.
  function paint(data) {
    paintWords(data.words);
    paintReplacements(data.replacements);
    paintSuggestions(data.suggestions);
  }

  async function change(method, params) {
    try {
      paint(await api(method, params));
      return true;
    } catch (error) {
      Hub.toastError(error);
      return false;
    }
  }

  // --- Words ---------------------------------------------------------------

  function paintWords(words) {
    const input = el('input', {
      class: 'input', type: 'text', spellcheck: 'false', placeholder: 'Add a word or name',
      'aria-label': 'Add a word', maxLength: 80,
    });
    const add = async () => {
      const word = input.value.trim();
      if (word && await change('dictionary_add', { word })) {
        sections.words.querySelector('.add-row .input')?.focus();
      }
    };
    input.addEventListener('keydown', (event) => { if (event.key === 'Enter') add(); });

    const chips = words.length
      ? el('div', { class: 'chips' }, words.map((word) => removableChip(word, () => change('dictionary_remove', { word }))))
      : el('div', { class: 'faint', text: 'No words yet.' });

    sections.words.replaceChildren(
      el('h2', { class: 'section-title', text: 'Words' }),
      el('p', { class: 'section-hint', text: 'Names, brands and jargon Whisper should listen for. A short list works best.' }),
      chips,
      el('div', { class: 'add-row' }, input, el('button', { class: 'button', type: 'button', text: 'Add', on: { click: add } })));
  }

  function removableChip(label, remove) {
    const chip = el('span', { class: 'chip' }, label);
    chip.append(el('button', {
      class: 'chip-remove', type: 'button', 'aria-label': `Remove ${label}`,
      on: {
        click: async () => {
          chip.classList.add('leaving');
          if (!await remove()) chip.classList.remove('leaving');
        },
      },
    }, Hub.icon('close')));
    return chip;
  }

  // --- Replacements --------------------------------------------------------

  function paintReplacements(replacements) {
    const heard = el('input', { class: 'input', type: 'text', spellcheck: 'false', placeholder: 'When it writes', 'aria-label': 'When it writes', maxLength: 80 });
    const fixed = el('input', { class: 'input', type: 'text', spellcheck: 'false', placeholder: 'Write instead', 'aria-label': 'Write instead', maxLength: 80 });
    const add = async () => {
      if (!heard.value.trim() || !fixed.value.trim()) {
        (heard.value.trim() ? fixed : heard).focus();
        return;
      }
      if (await change('replacement_add', { heard: heard.value.trim(), fixed: fixed.value.trim() })) {
        sections.replacements.querySelector('.replacement-add .input')?.focus();
      }
    };
    for (const input of [heard, fixed]) input.addEventListener('keydown', (event) => { if (event.key === 'Enter') add(); });

    const rows = replacements.length
      ? el('div', { class: 'replacements' }, replacements.map((item) => {
          const row = el('div', { class: 'replacement' },
            el('span', { class: 'heard', text: item.from }),
            el('span', { class: 'arrow', text: '→' }),
            el('span', { class: 'fixed', text: item.to }));
          row.append(Hub.iconButton('close', `Remove ${item.from}`, async () => {
            row.classList.add('leaving');
            if (!await change('replacement_remove', { heard: item.from })) row.classList.remove('leaving');
          }));
          return row;
        }))
      : el('div', { class: 'faint', text: 'No replacements yet.' });

    sections.replacements.replaceChildren(
      el('h2', { class: 'section-title', text: 'Replacements' }),
      el('p', { class: 'section-hint', text: 'Words Whisper keeps getting wrong, and what to write instead. Fixing a dictation in History adds these for you.' }),
      rows,
      el('div', { class: 'replacement-add' },
        heard, el('span', { class: 'arrow', text: '→' }), fixed,
        el('button', { class: 'button', type: 'button', text: 'Add', on: { click: add } })));
  }

  // --- Suggestions ---------------------------------------------------------

  function paintSuggestions(suggestions) {
    sections.suggestions.hidden = !suggestions.length;
    sections.suggestions.replaceChildren(
      el('h2', { class: 'section-title', text: 'Suggestions' }),
      el('p', { class: 'section-hint', text: "Words you say often that aren't in your dictionary yet." }),
      el('div', { class: 'chips' }, suggestions.map((suggestion) => el('span', { class: 'chip suggestion' },
        suggestion.word,
        el('span', { class: 'count', text: `${suggestion.count}×` }),
        el('button', {
          class: 'chip-add', type: 'button', 'aria-label': `Add ${suggestion.word}`,
          on: { click: () => change('dictionary_add', { word: suggestion.word }) },
        }, Hub.icon('plus'))))));
  }

  return { title: 'Dictionary', mount };
})();
