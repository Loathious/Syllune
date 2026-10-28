// pages/styles.js
// How dictation is written in each kind of app: a style per shipped app rule
// (chat, email, documents, AI assistants, code editors, terminals) plus one
// for everywhere else. Each rule can also carry extra instructions for the
// AI edit and its own list of apps. Changes save as you make them.

'use strict';

Hub.pages.styles = (() => {
  const { el, api } = Hub;
  const MAX_INSTRUCTIONS = 500;

  const STYLE_LABELS = {
    '': 'Auto', formal: 'Formal', casual: 'Casual', very_casual: 'Very casual', verbatim: 'Verbatim', prompt: 'Prompt',
  };
  const STYLE_DESCRIPTIONS = {
    '': "Whisper's own punctuation, tidied by the AI edit. Nothing forced.",
    formal: 'Full sentences with capitals and punctuation.',
    casual: 'Capitals and commas, without the stiff final period.',
    very_casual: 'All lowercase, like texting. Acronyms and your own words keep their case.',
    verbatim: 'Exactly as spoken. No added capitals, periods or lists.',
    prompt: 'Longer requests are laid out as a clear prompt: context, task, then details.',
  };

  const styleLabel = (name) => STYLE_LABELS[name] ?? name.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase());
  const styleDescription = (name) => STYLE_DESCRIPTIONS[name] ?? 'Your own style, from the settings file.';

  // "discord.exe" → "Discord"
  function appName(match) {
    const name = String(match).replace(/\.exe$/i, '');
    return name.charAt(0).toUpperCase() + name.slice(1);
  }

  async function mount() {
    const data = await api('styles');
    return Hub.page('Styles', null,
      el('p', { class: 'page-sub', text: "How your words are written, depending on the app you're in." }),
      defaultCard(data),
      data.rules.map((rule) => ruleCard(rule, data.styles)));
  }

  // --- Style picker --------------------------------------------------------

  // Segmented control plus a description that crossfades with the choice.
  function stylePicker(names, value, label, save) {
    const options = names.map((name) => ({ value: name, label: styleLabel(name) }));
    const control = Hub.segmented({ options, value, label, onChange: save });
    const descriptions = new Map(names.map((name) => [name, el('span', { text: styleDescription(name) })]));
    const description = el('div', { class: 'style-description' }, [...descriptions.values()]);
    const paint = (current) => descriptions.forEach((node, name) => node.classList.toggle('current', name === current));
    control.onPaint = paint;
    paint(value);
    return el('div', {}, el('div', { class: 'style-picker' }, control.node), description);
  }

  function defaultCard(data) {
    const names = ['', ...data.styles];
    return el('div', { class: 'panel style-card' },
      el('div', { class: 'label-row' }, el('h2', { class: 'section-title', text: 'Everywhere else' })),
      el('div', { class: 'style-apps', text: 'Apps without a style of their own' }),
      stylePicker(names, data.default_style, 'Style everywhere else',
        (style) => api('style_set', { rule_id: 'default', style })));
  }

  // --- One app rule ----------------------------------------------------------

  function ruleCard(rule, styles) {
    const appsLine = el('div', { class: 'style-apps' });
    const paintApps = (apps) => {
      appsLine.replaceChildren(...[...new Set(apps)].map(app => Hub.appBadge(app, appName(app))));
    };
    paintApps(rule.apps);

    const more = el('div', { class: 'style-more', hidden: true },
      instructionsField(rule),
      appsField(rule, paintApps));
    const disclosure = el('button', {
      class: 'disclosure', type: 'button', 'aria-expanded': 'false',
      on: {
        click: () => {
          const open = more.hidden;
          more.hidden = !open;
          disclosure.setAttribute('aria-expanded', String(open));
        },
      },
    }, Hub.icon('chevron'), 'Instructions and apps');

    return el('div', { class: 'panel style-card' },
      el('div', { class: 'label-row' }, el('h2', { class: 'section-title', text: rule.label })),
      appsLine,
      stylePicker(styles, rule.style, `Style in ${rule.label}`,
        (style) => api('style_set', { rule_id: rule.id, style })),
      disclosure,
      more);
  }

  // Extra instructions for the AI edit in these apps; saved as you pause.
  function instructionsField(rule) {
    const textarea = el('textarea', {
      class: 'textarea', maxLength: MAX_INSTRUCTIONS, rows: 3, spellcheck: 'true',
      placeholder: 'For example: keep it short, no greetings.',
      'aria-label': `AI instructions for ${rule.label}`,
    });
    textarea.value = rule.instructions;
    let saved = rule.instructions;
    const save = Hub.debounce(async () => {
      const value = textarea.value.trim();
      if (value === saved) return;
      try {
        await api('style_set', { rule_id: rule.id, instructions: value });
        saved = value;
      } catch (error) {
        Hub.toastError(error);
      }
    }, 800);
    textarea.addEventListener('input', save);
    textarea.addEventListener('blur', save.flush);
    return el('div', { class: 'field' },
      el('label', { class: 'field-label', text: 'Extra instructions for the AI edit' }),
      textarea);
  }

  // The apps this rule covers, as removable chips and an add box.
  function appsField(rule, paintApps) {
    let apps = [...rule.apps];
    const chips = el('div', { class: 'chips' });
    const input = el('input', {
      class: 'input', type: 'text', spellcheck: 'false',
      placeholder: 'Add an app, like obsidian.exe', 'aria-label': `Add an app to ${rule.label}`,
    });

    async function saveApps(next) {
      const result = await api('rule_apps_set', { rule_id: rule.id, apps: next });
      const updated = result.rules.find((other) => other.id === rule.id);
      apps = updated ? updated.apps : next;
      paint();
      paintApps(apps);
    }

    function paint() {
      chips.replaceChildren(...apps.map((app) => {
        const chip = el('span', { class: 'chip' }, Hub.appBadge(app, appName(app)));
        if (apps.length > 1) {
          chip.append(el('button', {
            class: 'chip-remove', type: 'button', 'aria-label': `Remove ${app}`,
            on: {
              click: async () => {
                chip.classList.add('leaving');
                try {
                  await saveApps(apps.filter((other) => other !== app));
                } catch (error) {
                  chip.classList.remove('leaving');
                  Hub.toastError(error);
                }
              },
            },
          }, Hub.icon('close')));
        } else {
          chip.classList.add('plain');
        }
        return chip;
      }));
    }

    async function add() {
      const app = input.value.trim();
      if (!app) return;
      try {
        await saveApps([...apps, app]);
        input.value = '';
      } catch (error) {
        Hub.toastError(error);
      }
    }
    input.addEventListener('keydown', (event) => { if (event.key === 'Enter') add(); });

    paint();
    return el('div', { class: 'field' },
      el('span', { class: 'field-label', text: 'Apps (matched by program name or window title)' }),
      chips,
      el('div', { class: 'add-row' }, input, el('button', { class: 'button', type: 'button', text: 'Add', on: { click: add } })));
  }

  return { title: 'Styles', mount };
})();
