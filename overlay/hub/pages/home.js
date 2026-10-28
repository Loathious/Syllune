// pages/home.js
// Home: this week at a glance (words, time saved, speaking pace), the last
// two weeks as a small bar chart, where you dictate most, and your latest
// dictations. Plain numbers only: no streaks, scores or goals.

'use strict';

Hub.pages.home = (() => {
  const { el, api } = Hub;
  const QUALITY_LABELS = { fast: 'Fast', balanced: 'Balanced', best: 'Best' };

  async function mount() {
    const data = await api('overview');
    return Hub.page('Home', [statusLine(data.status)],
      statCells(data),
      activity(data.days || []),
      el('div', { class: 'home-grid' }, topApps(data.top_apps || []), recent(data.recent || [])));
  }

  // --- Status --------------------------------------------------------------

  // "large-v3-turbo · Balanced · gemma3:4b ready", with a dot for the AI edit.
  function statusLine(status) {
    let dot = 'dot';
    let ai;
    if (status.game_mode) ai = 'Game mode, AI edit paused';
    else if (status.quality === 'fast') ai = 'No AI edit on Fast';
    else if (!status.ai_enabled) ai = 'AI edit off';
    else if (status.ai_reachable) { ai = `${status.ai_model} ready`; dot = 'dot on'; }
    else { ai = "Ollama isn't running"; dot = 'dot off'; }
    const parts = [status.model, QUALITY_LABELS[status.quality], ai].filter(Boolean);
    return el('span', { class: 'status-line' }, el('span', { class: dot }), parts.join(' · '));
  }

  // --- Numbers -------------------------------------------------------------

  function statCells(data) {
    const pace = Math.round(data.pace_wpm || 0);
    return el('div', { class: 'stats' },
      statCell('This week', Hub.formatNumber(data.week_words), 'words', Hub.plural(data.week_dictations || 0, 'dictation')),
      statCell('Time saved this week', Hub.formatMinutes(data.week_minutes_saved), '',
        `${Hub.formatMinutes(data.minutes_saved)} in total`),
      statCell('Speaking pace', pace ? String(pace) : '–', pace ? 'wpm' : '',
        `You type about ${data.typing_wpm} wpm`));
  }

  function statCell(label, value, unit, sub) {
    return el('div', { class: 'panel stat' },
      el('div', { class: 'stat-label', text: label }),
      el('div', { class: 'stat-value' }, value, unit ? el('span', { class: 'stat-unit', text: unit }) : null),
      el('div', { class: 'stat-sub', text: sub }));
  }

  // --- Last 14 days --------------------------------------------------------
  // One series, so no legend: the title names it. Today's bar is full
  // strength, the rest are quieter. Each column is the hover target, so a
  // short bar is as easy to point at as a tall one.

  function activity(days) {
    const max = Math.max(1, ...days.map((day) => day.words));
    const total = days.reduce((sum, day) => sum + day.words, 0);
    const tip = el('div', { class: 'chart-tip', 'aria-hidden': 'true' });
    const chart = el('div', {
      class: 'chart', role: 'img',
      'aria-label': `Words per day over the last 14 days, ${Hub.formatNumber(total)} in total`,
    });
    const axis = el('div', { class: 'chart-axis', 'aria-hidden': 'true' });

    days.forEach((day, i) => {
      const date = Hub.parseDay(day.date);
      const isToday = i === days.length - 1;
      const bar = el('div', { class: day.words ? 'chart-bar' : 'chart-bar zero' });
      if (day.words) bar.style.height = `${Math.max(3, (day.words / max) * 100)}%`;
      const column = el('div', { class: isToday ? 'chart-column today' : 'chart-column' }, bar);
      column.addEventListener('mouseenter', () => showTip(tip, chart, column, bar, date, day.words));
      column.addEventListener('mouseleave', () => tip.classList.remove('shown'));
      chart.append(column);
      axis.append(el('span', { class: isToday ? 'muted' : '', text: String(date.getDate()) }));
    });
    chart.append(tip);

    return el('div', { class: 'panel' },
      el('div', { class: 'label-row' },
        el('h2', { class: 'section-title', text: 'Last 14 days' }),
        el('span', { class: 'muted tabular', text: Hub.plural(total, 'word') })),
      chart, axis);
  }

  // Above the hovered bar, kept inside the chart's width.
  function showTip(tip, chart, column, bar, date, words) {
    tip.replaceChildren(
      date.toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short' }),
      el('span', { class: 'muted', text: words ? Hub.plural(words, 'word') : 'nothing' }));
    const width = tip.offsetWidth;
    const center = column.offsetLeft + column.offsetWidth / 2;
    const x = Math.min(Math.max(center - width / 2, 0), chart.clientWidth - width);
    tip.style.transform = `translateX(${Math.round(x)}px)`;
    tip.style.bottom = `${bar.offsetHeight + 8}px`;
    tip.classList.add('shown');
  }

  // --- Where you dictate ---------------------------------------------------

  function topApps(apps) {
    const max = Math.max(1, ...apps.map((app) => app.words));
    const rows = apps.length
      ? apps.map((app) => el('div', { class: 'app-row' },
          Hub.appBadge(app.app, app.label),
          el('div', { class: 'app-track' }, el('div', { class: 'app-fill', style: { width: `${(app.words / max) * 100}%` } })),
          el('span', { class: 'app-words', text: Hub.formatNumber(app.words) })))
      : [el('div', { class: 'muted', text: 'Nothing this week yet.' })];
    return el('div', { class: 'panel' },
      el('div', { class: 'label-row' },
        el('h2', { class: 'section-title', text: 'Where you dictate' }),
        el('span', { class: 'faint', text: 'this week' })),
      rows);
  }

  // --- Recent --------------------------------------------------------------

  function recent(items) {
    const list = items.length
      ? el('div', { class: 'recent' }, items.map((item) => el('button', {
          class: 'recent-item', type: 'button', title: 'Copy',
          on: { click: () => Hub.copy(item.text) },
        },
          el('div', { class: 'recent-text', text: item.text }),
          el('div', { class: 'recent-meta' }, Hub.formatWhen(Hub.parseTimestamp(item.timestamp)), item.app ? Hub.appBadge(item.app) : null))))
      : el('div', { class: 'muted', text: 'Your dictations will show up here.' });
    return el('div', { class: 'panel' },
      el('div', { class: 'label-row' },
        el('h2', { class: 'section-title', text: 'Recent' }),
        el('button', { class: 'button quiet', type: 'button', text: 'All history', on: { click: () => Hub.navigate('history') } })),
      list);
  }

  // A new dictation updates every number on the page.
  const refresh = Hub.debounce(() => Hub.reload(), 300);

  return {
    title: 'Home',
    mount,
    onEvent: (name) => { if (name === 'transcript') refresh(); },
  };
})();
