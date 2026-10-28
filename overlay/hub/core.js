// core.js
// Shared plumbing for the hub pages: calls to the engine, the page router,
// the toast, date and number formatting, and the few controls every page
// uses (toggle, segmented control, select, icons). Pages register themselves
// in `Hub.pages`; start.js opens the first one. Text is only ever set with
// textContent and styles only through CSSOM, so the strict CSP holds.

'use strict';

const Hub = (() => {
  const pages = {};

  // =========================================================================
  // Engine calls
  // =========================================================================

  // Resolves with hub_api's result, or rejects with its message.
  const api = (method, params) => window.hub.call(method, params);

  // =========================================================================
  // DOM helpers
  // =========================================================================

  // Assigned as properties; everything else becomes an attribute.
  const PROPERTY_KEYS = new Set(['value', 'disabled', 'hidden', 'checked', 'maxLength', 'tabIndex', 'rows']);

  // el('button', {class, text, on: {click}, style: {width: '4px'}, ...attrs}, ...children)
  function el(tag, props = {}, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(props)) {
      if (value === undefined || value === null || value === false) continue;
      if (key === 'class') node.className = value;
      else if (key === 'text') node.textContent = value;
      else if (key === 'on') for (const [name, fn] of Object.entries(value)) node.addEventListener(name, fn);
      else if (key === 'style') for (const [name, v] of Object.entries(value)) node.style.setProperty(name, v);
      else if (PROPERTY_KEYS.has(key)) node[key] = value;
      else node.setAttribute(key, value === true ? '' : String(value));
    }
    append(node, children);
    return node;
  }

  // Strings become text nodes; null/false children are skipped; nested
  // arrays (a page's list of cards) are flattened.
  function append(node, children) {
    for (const child of children.flat(Infinity)) {
      if (child === null || child === undefined || child === false) continue;
      node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
  }

  // Line icons on a 16px grid, drawn with the current text colour.
  const ICONS = {
    copy: ['M7 5.5h5a1.5 1.5 0 0 1 1.5 1.5v5a1.5 1.5 0 0 1-1.5 1.5H7A1.5 1.5 0 0 1 5.5 12V7A1.5 1.5 0 0 1 7 5.5z',
           'M10.5 5.5V4A1.5 1.5 0 0 0 9 2.5H4A1.5 1.5 0 0 0 2.5 4v5A1.5 1.5 0 0 0 4 10.5h1.5'],
    edit: ['M10.2 3.3a1.4 1.4 0 0 1 2 0l.5.5a1.4 1.4 0 0 1 0 2L6 12.5l-3 .6.6-3z'],
    trash: ['M3 4.5h10', 'M6.5 4.5V3h3v1.5', 'M4.5 4.5l.6 8.1a1 1 0 0 0 1 .9h3.8a1 1 0 0 0 1-.9l.6-8.1'],
    close: ['M4.5 4.5l7 7', 'M11.5 4.5l-7 7'],
    plus: ['M8 3.5v9', 'M3.5 8h9'],
    chevron: ['M6 4l4 4-4 4'],
    play: ['M5.5 3.8v8.4a.5.5 0 0 0 .77.42l6.3-4.2a.5.5 0 0 0 0-.84l-6.3-4.2a.5.5 0 0 0-.77.42z'],
    folder: ['M2.5 4.5a1 1 0 0 1 1-1h3l1.5 1.5h4.5a1 1 0 0 1 1 1v6a1 1 0 0 1-1 1h-9a1 1 0 0 1-1-1z'],
  };

  function icon(name) {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 16 16');
    svg.setAttribute('aria-hidden', 'true');
    for (const d of ICONS[name] || []) {
      const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      path.setAttribute('d', d);
      svg.append(path);
    }
    return svg;
  }

  function iconButton(name, label, onClick) {
    return el('button', { class: 'icon-button', type: 'button', title: label, 'aria-label': label, on: { click: onClick } }, icon(name));
  }

  // Names people know an app by, where the executable spells it otherwise.
  const APP_NAMES = {
    chatgpt: 'ChatGPT', msedge: 'Microsoft Edge', chrome: 'Google Chrome', code: 'VS Code', winword: 'Word',
    excel: 'Excel', powerpnt: 'PowerPoint', outlook: 'Outlook', olk: 'Outlook', 'ms-teams': 'Teams',
    explorer: 'File Explorer', windowsterminal: 'Terminal', whatsapp: 'WhatsApp', onenote: 'OneNote',
  };

  // Icons carry app identity; names remain available on hover and keyboard
  // focus. Only a sanitized executable leaf ever reaches the native resolver.
  function appBadge(executable, label) {
    const stem = String(executable || 'Application').replace(/\.exe$/i, '');
    const name = label || APP_NAMES[stem.toLowerCase()] || stem.replace(/^./, c => c.toUpperCase());
    const badge = el('span', {class: 'app-badge', 'data-name': name, role: 'img', 'aria-label': name, tabIndex: 0},
      el('span', {class: 'app-fallback', 'aria-hidden': 'true', text: name.replace(/[^\p{L}\p{N}]/gu, '').slice(0, 1)}));
    window.hub.appIcon?.(executable).then(source => {
      if (source && source.startsWith('data:image/')) badge.replaceChildren(el('img', {src: source, alt: '', width: 28, height: 28}));
    }).catch(() => {});
    return badge;
  }

  // The frame every page shares: title, optional actions on the right, body.
  function page(title, actions, ...children) {
    return el('div', { class: 'page' },
      el('header', { class: 'page-head' },
        el('h1', { class: 'page-title', text: title }),
        actions && actions.length ? el('div', { class: 'page-actions' }, actions) : null),
      children);
  }

  function debounce(fn, ms) {
    let timer = null;
    let lastArgs = [];
    const debounced = (...args) => {
      lastArgs = args;
      clearTimeout(timer);
      timer = setTimeout(() => { timer = null; fn(...lastArgs); }, ms);
    };
    // Runs a pending call now (on blur, or before the page goes away).
    debounced.flush = () => {
      if (timer === null) return;
      clearTimeout(timer);
      timer = null;
      fn(...lastArgs);
    };
    return debounced;
  }

  // =========================================================================
  // Formatting
  // =========================================================================
  // English labels to match the UI, 24-hour time.

  const LOCALE = 'en-GB';

  // Transcript timestamps are local ISO time without an offset, which Date
  // reads as local time.
  const parseTimestamp = (timestamp) => new Date(timestamp);

  // "2026-10-27" as a local date.
  function parseDay(isoDay) {
    const [year, month, day] = isoDay.split('-').map(Number);
    return new Date(year, month - 1, day);
  }

  const startOfDay = (date) => new Date(date.getFullYear(), date.getMonth(), date.getDate());

  function daysAgo(date) {
    return Math.round((startOfDay(new Date()) - startOfDay(date)) / 86400000);
  }

  const formatTime = (date) => date.toLocaleTimeString(LOCALE, { hour: '2-digit', minute: '2-digit' });

  function formatDayHeading(date) {
    const ago = daysAgo(date);
    if (ago === 0) return 'Today';
    if (ago === 1) return 'Yesterday';
    const sameYear = date.getFullYear() === new Date().getFullYear();
    return date.toLocaleDateString(LOCALE, { weekday: 'long', day: 'numeric', month: 'long', year: sameYear ? undefined : 'numeric' });
  }

  // Short "when" for one dictation: "14:03", "Yesterday 14:03", "26 Oct 14:03".
  function formatWhen(date) {
    const ago = daysAgo(date);
    if (ago === 0) return formatTime(date);
    if (ago === 1) return `Yesterday ${formatTime(date)}`;
    return `${date.toLocaleDateString(LOCALE, { day: 'numeric', month: 'short' })} ${formatTime(date)}`;
  }

  const formatNumber = (n) => Math.round(n || 0).toLocaleString(LOCALE);

  function formatMinutes(minutes) {
    minutes = Math.max(0, minutes || 0);
    if (minutes < 1) return minutes > 0 ? '< 1 min' : '0 min';
    if (minutes < 60) return `${Math.round(minutes)} min`;
    const hours = Math.floor(minutes / 60);
    const rest = Math.round(minutes - hours * 60);
    return rest ? `${hours} h ${rest} min` : `${hours} h`;
  }

  const plural = (n, word, many = `${word}s`) => `${formatNumber(n)} ${n === 1 ? word : many}`;

  // =========================================================================
  // Toast
  // =========================================================================
  // One at a time: a new message replaces the current one in place.

  const toastNode = document.getElementById('toast');
  const toastText = document.getElementById('toast-text');
  const toastAction = document.getElementById('toast-action');
  let toastTimer = null;
  let toastRun = null;
  let toastDuration = 0;

  // `action` is an optional {label, run} button, e.g. Undo.
  function toast(message, action) {
    toastText.textContent = message;
    toastRun = action ? action.run : null;
    toastAction.textContent = action ? action.label : '';
    toastNode.classList.toggle('has-action', !!action);
    toastNode.classList.add('shown');
    toastDuration = action ? 6000 : 2400;
    restartToastTimer(toastDuration);
  }

  function hideToast() {
    clearTimeout(toastTimer);
    toastNode.classList.remove('shown');
    toastRun = null;
  }

  function restartToastTimer(ms) {
    clearTimeout(toastTimer);
    toastTimer = setTimeout(hideToast, ms);
  }

  const toastError = (error) => toast(error && error.message ? error.message : String(error));

  toastAction.addEventListener('click', () => {
    const run = toastRun;
    hideToast();
    if (run) Promise.resolve(run()).catch(toastError);
  });
  // A toast you're reaching for doesn't vanish under the pointer.
  toastNode.addEventListener('mouseenter', () => clearTimeout(toastTimer));
  toastNode.addEventListener('mouseleave', () => { if (toastNode.classList.contains('shown')) restartToastTimer(1800); });

  async function copy(text, message = 'Copied') {
    await window.hub.copy(text);
    toast(message);
  }

  // =========================================================================
  // Controls
  // =========================================================================
  // Every control updates at once and writes in the background; if the
  // engine refuses, it snaps back and the toast says why.

  function toggle({ checked, label, onChange, disabled }) {
    const node = el('button', { class: 'toggle', type: 'button', role: 'switch', 'aria-label': label, disabled });
    let value = !!checked;
    const paint = () => node.setAttribute('aria-checked', String(value));
    node.addEventListener('click', async () => {
      value = !value;
      paint();
      try {
        await onChange(value);
      } catch (error) {
        value = !value;
        paint();
        toastError(error);
      }
    });
    paint();
    return node;
  }

  // Equal-width segments with a thumb that slides to the chosen one. An
  // unknown value leaves nothing selected.
  function segmented({ options, value, label, onChange }) {
    const thumb = el('span', { class: 'segmented-thumb', 'aria-hidden': 'true' });
    const root = el('div', { class: 'segmented', role: 'radiogroup', 'aria-label': label }, thumb);
    const buttons = options.map((option) => el('button', {
      class: 'segment', type: 'button', role: 'radio', text: option.label,
      on: { click: () => choose(option.value) },
    }));
    root.append(...buttons);
    thumb.style.width = `calc((100% - 6px) / ${options.length})`;
    let current = value;

    function paint() {
      const index = options.findIndex((option) => option.value === current);
      buttons.forEach((button, i) => {
        button.setAttribute('aria-checked', String(i === index));
        button.tabIndex = i === Math.max(index, 0) ? 0 : -1;
      });
      thumb.style.opacity = index < 0 ? '0' : '1';
      if (index >= 0) thumb.style.transform = `translateX(${index * 100}%)`;
    }

    async function choose(next) {
      if (next === current) return;
      const previous = current;
      current = next;
      paint();
      control.onPaint?.(current);
      try {
        await onChange(next);
      } catch (error) {
        current = previous;
        paint();
        control.onPaint?.(current);
        toastError(error);
      }
    }

    // Arrow keys move the choice, as in a native radio group.
    root.addEventListener('keydown', (event) => {
      if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
      event.preventDefault();
      const index = Math.max(0, options.findIndex((option) => option.value === current));
      const next = (index + (event.key === 'ArrowRight' ? 1 : -1) + options.length) % options.length;
      buttons[next].focus();
      choose(options[next].value);
    });

    paint();
    const control = { node: root, get value() { return current; }, onPaint: null };
    return control;
  }

  // A native select whose options can carry any value (null, numbers).
  function select({ options, value, label, onChange, disabled }) {
    const node = el('select', { class: 'select', 'aria-label': label, disabled });
    options.forEach((option, i) => node.append(el('option', { value: String(i), text: option.label })));
    let index = options.findIndex((option) => option.value === value);
    node.value = String(Math.max(index, 0));
    node.addEventListener('change', async () => {
      const previous = index;
      index = Number(node.value);
      try {
        await onChange(options[index].value);
      } catch (error) {
        index = previous;
        node.value = String(Math.max(index, 0));
        toastError(error);
      }
    });
    return node;
  }

  // =========================================================================
  // Router
  // =========================================================================

  const content = document.getElementById('content');
  const nav = document.getElementById('nav');
  const indicator = document.getElementById('nav-indicator');
  const navItems = [...nav.querySelectorAll('.nav-item')];
  let currentName = null;
  let currentPage = null;
  let navigation = 0;              // only the latest navigation may mount

  async function navigate(name) {
    if (!pages[name]) name = 'home';
    if (name === currentName) return;
    const ticket = ++navigation;
    currentPage?.leave?.();
    currentName = name;
    markNav(name);
    const target = pages[name];
    const node = await buildPage(target);
    if (ticket !== navigation) return;
    currentPage = target;
    content.classList.toggle('fill', !!target.fill);
    content.replaceChildren(node);
    content.scrollTop = 0;
    target.mounted?.();
  }

  async function buildPage(target) {
    try {
      return await target.mount();
    } catch (error) {
      return page(target.title, null,
        el('div', { class: 'empty' },
          el('div', { class: 'empty-title', text: "Couldn't load this page" }),
          el('div', { class: 'error-line', text: error.message || String(error) }),
          el('div', { style: { 'margin-top': '16px' } },
            el('button', { class: 'button', type: 'button', text: 'Try again', on: { click: reload } }))));
    }
  }

  // Rebuilds the open page in place after its data changed: same scroll
  // position, no entrance fade.
  async function reload() {
    const target = currentPage || pages[currentName];
    if (!target) return;
    const ticket = navigation;
    const node = await buildPage(target);
    if (ticket !== navigation) return;
    node.style.setProperty('transition', 'none');
    const scroll = content.scrollTop;
    currentPage = target;
    content.replaceChildren(node);
    content.scrollTop = scroll;
    requestAnimationFrame(() => node.style.removeProperty('transition'));
    target.mounted?.();
  }

  function markNav(name) {
    let active = null;
    for (const item of navItems) {
      const on = item.dataset.page === name;
      if (on) { item.setAttribute('aria-current', 'page'); active = item; }
      else item.removeAttribute('aria-current');
    }
    if (!active) return;
    indicator.style.transform = `translateY(${active.offsetTop}px)`;
    indicator.classList.add('placed');
  }

  function start(firstPage) {
    for (const item of navItems) item.addEventListener('click', () => navigate(item.dataset.page));
    window.hub.onNavigate(navigate);
    window.hub.onEvent((name) => currentPage?.onEvent?.(name));
    window.addEventListener('beforeunload', () => currentPage?.leave?.());
    document.addEventListener('keydown', (event) => {
      if (event.ctrlKey && !event.shiftKey && !event.altKey && event.key.toLowerCase() === 'f' && currentPage?.focusSearch) {
        event.preventDefault();
        currentPage.focusSearch();
      }
    });
    navigate(firstPage);
    // The highlight is placed without motion first, then slides from then on.
    requestAnimationFrame(() => requestAnimationFrame(() => nav.classList.add('animated')));
  }

  return {
    pages, api, el, icon, iconButton, appBadge, page, debounce,
    parseTimestamp, parseDay, daysAgo, formatTime, formatDayHeading, formatWhen,
    formatNumber, formatMinutes, plural,
    toast, toastError, copy,
    toggle, segmented, select,
    navigate, reload, start,
  };
})();
