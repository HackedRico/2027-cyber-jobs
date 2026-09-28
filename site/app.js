// The board page: fetches listings.json once, then filters in memory. Every
// filter lives in the query string so a filtered view can be shared.
(() => {
  'use strict';

  const REPO = 'https://github.com/HackedRico/2027-cyber-jobs';
  const TYPES = ['intern', 'newgrad', 'earlycareer'];
  const TYPE_NOUNS = {
    intern: ['internship', 'internships'],
    newgrad: ['new-grad role', 'new-grad roles'],
    earlycareer: ['early-career role', 'early-career roles'],
  };
  const STATE_NAMES = {
    AL: 'Alabama', AK: 'Alaska', AZ: 'Arizona', AR: 'Arkansas', CA: 'California',
    CO: 'Colorado', CT: 'Connecticut', DE: 'Delaware', DC: 'District of Columbia',
    FL: 'Florida', GA: 'Georgia', HI: 'Hawaii', ID: 'Idaho', IL: 'Illinois',
    IN: 'Indiana', IA: 'Iowa', KS: 'Kansas', KY: 'Kentucky', LA: 'Louisiana',
    ME: 'Maine', MD: 'Maryland', MA: 'Massachusetts', MI: 'Michigan', MN: 'Minnesota',
    MS: 'Mississippi', MO: 'Missouri', MT: 'Montana', NE: 'Nebraska', NV: 'Nevada',
    NH: 'New Hampshire', NJ: 'New Jersey', NM: 'New Mexico', NY: 'New York',
    NC: 'North Carolina', ND: 'North Dakota', OH: 'Ohio', OK: 'Oklahoma', OR: 'Oregon',
    PA: 'Pennsylvania', RI: 'Rhode Island', SC: 'South Carolina', SD: 'South Dakota',
    TN: 'Tennessee', TX: 'Texas', UT: 'Utah', VT: 'Vermont', VA: 'Virginia',
    WA: 'Washington', WV: 'West Virginia', WI: 'Wisconsin', WY: 'Wyoming',
    PR: 'Puerto Rico', GU: 'Guam', AS: 'American Samoa', VI: 'U.S. Virgin Islands',
    MP: 'Northern Mariana Islands',
  };
  const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const DEFAULTS = { type: 'intern', q: '', cat: [], state: '', days: '', noclear: false, closed: false, sort: '' };

  const $ = (id) => document.getElementById(id);
  const el = {
    tabs: [...document.querySelectorAll('[role="tab"]')],
    panel: $('panel'),
    q: $('q'),
    cats: $('cats'),
    state: $('state'),
    days: $('days'),
    noclear: $('noclear'),
    hideclosed: $('hideclosed'),
    sort: $('sort'),
    count: $('result-count'),
    rows: $('results'),
    empty: $('empty'),
    filters: $('filters'),
    toggle: $('filters-toggle'),
    badge: $('filter-badge'),
    updated: $('updated'),
  };

  let rows = [];
  let categories = [];
  let state = { ...DEFAULTS };
  let newDays = 7;
  const today = startOfDay(new Date());

  function startOfDay(d) { return new Date(d.getFullYear(), d.getMonth(), d.getDate()); }

  function parseDay(iso) {
    const [y, m, d] = iso.split('-').map(Number);
    return new Date(y, m - 1, d);
  }

  function daysAgo(iso) { return Math.round((today - parseDay(iso)) / 86400000); }

  function shortDate(iso) {
    const d = parseDay(iso);
    return `${MONTHS[d.getMonth()]} ${d.getDate()}`;
  }

  function slug(name) {
    return name.toLowerCase().replace(/&/g, 'and').replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
  }

  const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  function esc(text) { return String(text).replace(/[&<>"']/g, (c) => ESC[c]); }

  function safeUrl(url) { return /^https?:\/\/\S+$/.test(url || '') ? url : ''; }

  // ---- URL state ---------------------------------------------------------

  function readUrl() {
    const p = new URLSearchParams(location.search);
    const type = p.get('type');
    const slugs = new Set(categories.map(slug));
    return {
      type: TYPES.includes(type) ? type : DEFAULTS.type,
      q: (p.get('q') || '').slice(0, 120),
      cat: (p.get('cat') || '').split(',').filter((c) => slugs.has(c)),
      state: p.get('state') || '',
      days: ['7', '30'].includes(p.get('days')) ? p.get('days') : '',
      noclear: p.get('noclear') === '1',
      closed: p.get('closed') === '1',
      sort: p.get('sort') === 'company' ? 'company' : '',
    };
  }

  function writeUrl() {
    const p = new URLSearchParams();
    if (state.type !== DEFAULTS.type) p.set('type', state.type);
    if (state.q.trim()) p.set('q', state.q.trim());
    if (state.cat.length) p.set('cat', state.cat.join(','));
    if (state.state) p.set('state', state.state);
    if (state.days) p.set('days', state.days);
    if (state.noclear) p.set('noclear', '1');
    if (state.closed) p.set('closed', '1');
    if (state.sort) p.set('sort', state.sort);
    const qs = p.toString().replace(/%2C/g, ',');
    const url = location.pathname + (qs ? `?${qs}` : '') + location.hash;
    if (url !== location.pathname + location.search + location.hash) {
      history.replaceState(null, '', url);
    }
  }

  function syncControls() {
    el.tabs.forEach((tab) => {
      const on = tab.dataset.type === state.type;
      tab.setAttribute('aria-selected', String(on));
      tab.tabIndex = on ? 0 : -1;
    });
    el.panel.setAttribute('aria-labelledby', `tab-${state.type}`);
    if (el.q.value !== state.q) el.q.value = state.q;
    el.cats.querySelectorAll('.chip').forEach((chip) => {
      chip.setAttribute('aria-pressed', String(state.cat.includes(chip.dataset.cat)));
    });
    el.state.value = [...el.state.options].some((o) => o.value === state.state) ? state.state : '';
    el.days.value = state.days;
    el.noclear.checked = state.noclear;
    el.hideclosed.checked = !state.closed;
    el.sort.value = state.sort;
    const active = state.cat.length + (state.state ? 1 : 0) + (state.days ? 1 : 0)
      + (state.noclear ? 1 : 0) + (state.closed ? 1 : 0);
    el.badge.hidden = active === 0;
    el.badge.textContent = active;
  }

  // ---- Filtering ---------------------------------------------------------

  // Words, not substrings: "soc" must not match "Associate".
  function words(text) {
    return text.toLowerCase().normalize('NFKD').replace(/[\u0300-\u036f]/g, '')
      .split(/[^a-z0-9+#]+/).filter(Boolean);
  }

  function terms() { return words(state.q).map((w) => ` ${w}`); }

  function matches(row, needles, catSet) {
    if (!state.closed && row.closed) return false;
    if (state.noclear && row.clearance) return false;
    if (catSet.size && !catSet.has(row.slug)) return false;
    if (state.state === 'remote' ? !row.remote : state.state && !row.states.includes(state.state)) return false;
    if (state.days && row.age > Number(state.days)) return false;
    for (const n of needles) if (!row.hay.includes(n)) return false;
    return true;
  }

  function byCompany(a, b) {
    return a.company.localeCompare(b.company, 'en', { sensitivity: 'base' })
      || b.date_added.localeCompare(a.date_added);
  }

  function byDate(a, b) {
    return (a.closed ? 1 : 0) - (b.closed ? 1 : 0)
      || b.date_added.localeCompare(a.date_added)
      || a.company.localeCompare(b.company, 'en', { sensitivity: 'base' });
  }

  // ---- Rendering ---------------------------------------------------------

  const ARROW = '<svg aria-hidden="true" viewBox="0 0 16 16" width="14" height="14"><path d="M5 3h8v8M13 3 3 13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>';

  function reportUrl(row) {
    const p = new URLSearchParams({
      template: 'report-listing.yml',
      title: `[REPORT] ${row.company}: ${row.role}`,
      company: row.company,
      role: row.role,
    });
    if (row.url) p.set('listing', row.url);
    return `${REPO}/issues/new?${p}`;
  }

  function locationHtml(loc) {
    const parts = loc.split(';').map((s) => s.trim()).filter(Boolean);
    if (parts.length <= 1) return `<span class="loc">${esc(loc || 'United States')}</span>`;
    const rest = parts.slice(1).map((p) => `<li>${esc(p)}</li>`).join('');
    return `<details class="locs"><summary>${esc(parts[0])}<span class="more">+${parts.length - 1} more</span></summary><ul>${rest}</ul></details>`;
  }

  function rowHtml(row) {
    const isNew = !row.closed && row.age <= newDays;
    const cls = ['row', isNew ? 'is-new' : '', row.closed ? 'is-closed' : ''].filter(Boolean).join(' ');
    const status = row.closed
      ? `<span class="closed-tag">🔒 Closed${row.closed_date ? ` ${esc(shortDate(row.closed_date))}` : ''}</span>`
      : (isNew ? '<span class="new" title="Added in the last 7 days">🆕<span class="sr-only">New</span></span>' : '');
    const flag = row.clearance
      ? '<span class="flag" title="Mentions a security clearance, public trust or U.S. citizenship"><span aria-hidden="true">🇺🇸</span> Clearance or citizenship</span>'
      : '';
    const url = safeUrl(row.url);
    const apply = url && !row.closed
      ? `<a class="apply" href="${esc(url)}" target="_blank" rel="noopener noreferrer">Apply<span class="sr-only">: ${esc(row.company)}, ${esc(row.role)}</span>${ARROW}</a>`
      : '';
    return `<li class="${cls}" data-id="${esc(row.id)}">
<div class="row-head"><span class="company">${esc(row.company)}</span>${status}<time class="date" datetime="${esc(row.date_added)}" title="Added ${esc(row.date_added)}">Added ${esc(shortDate(row.date_added))}</time></div>
<h3 class="role">${esc(row.role)}${flag}</h3>
<div class="meta"><span class="cat">${esc(row.category)}</span>${locationHtml(row.location)}</div>
<div class="actions">${apply}<a class="report" href="${esc(reportUrl(row))}" target="_blank" rel="noopener noreferrer">Report<span class="sr-only"> ${esc(row.company)}, ${esc(row.role)}</span></a></div>
</li>`;
  }

  function describe(n, type) {
    const [one, many] = TYPE_NOUNS[type];
    return `<strong>${n}</strong> ${n === 1 ? one : many}`;
  }

  let firstPaint = true;

  function render() {
    const needles = terms();
    const catSet = new Set(state.cat);
    const counts = { intern: 0, newgrad: 0, earlycareer: 0 };
    const shown = [];
    for (const row of rows) {
      if (!matches(row, needles, catSet)) continue;
      counts[row.type] += 1;
      if (row.type === state.type) shown.push(row);
    }
    shown.sort(state.sort === 'company' ? byCompany : byDate);

    document.querySelectorAll('[data-count]').forEach((node) => {
      node.textContent = counts[node.dataset.count];
    });
    el.tabs.forEach((tab) => {
      const t = tab.dataset.type;
      tab.setAttribute('aria-label', `${tab.querySelector('.tab-label').textContent}, ${counts[t]} ${counts[t] === 1 ? 'role' : 'roles'}`);
    });
    const total = rows.filter((r) => r.type === state.type && (state.closed || !r.closed)).length;
    const filtered = shown.length !== total;
    el.count.innerHTML = filtered
      ? `${describe(shown.length, state.type)} of ${total}`
      : describe(shown.length, state.type);

    el.rows.classList.toggle('intro', firstPaint);
    el.rows.innerHTML = shown.map(rowHtml).join('');
    if (firstPaint) {
      [...el.rows.children].slice(0, 14).forEach((li, i) => { li.style.animationDelay = `${i * 28}ms`; });
      firstPaint = false;
    }
    el.empty.hidden = shown.length !== 0;
    if (!shown.length) {
      const others = TYPES.filter((t) => t !== state.type && counts[t]);
      $('empty-hint').textContent = others.length ? 'Other tabs have matches:' : 'Try fewer filters.';
      $('empty-jump').innerHTML = others.map((t) => (
        `<button type="button" class="jump" data-type="${t}">Show ${counts[t]} ${TYPE_NOUNS[t][counts[t] === 1 ? 0 : 1]}</button>`
      )).join('');
    }
    syncControls();
    writeUrl();
  }

  // ---- Setup -------------------------------------------------------------

  function buildControls() {
    const present = new Set(rows.map((r) => r.category));
    el.cats.innerHTML = categories.filter((c) => present.has(c)).map((c) => (
      `<button type="button" class="chip" data-cat="${esc(slug(c))}" aria-pressed="false">${esc(c)}</button>`
    )).join('');

    const codes = new Set(rows.flatMap((r) => r.states));
    const sorted = [...codes].sort((a, b) => (STATE_NAMES[a] || a).localeCompare(STATE_NAMES[b] || b));
    for (const code of sorted) {
      const opt = document.createElement('option');
      opt.value = code;
      opt.textContent = STATE_NAMES[code] ? `${STATE_NAMES[code]} (${code})` : code;
      el.state.append(opt);
    }
  }

  function set(patch) {
    state = { ...state, ...patch };
    render();
  }

  function bind() {
    el.tabs.forEach((tab, i) => {
      tab.addEventListener('click', () => set({ type: tab.dataset.type }));
      tab.addEventListener('keydown', (e) => {
        const step = { ArrowRight: 1, ArrowLeft: -1 }[e.key];
        let next = null;
        if (step) next = el.tabs[(i + step + el.tabs.length) % el.tabs.length];
        if (e.key === 'Home') next = el.tabs[0];
        if (e.key === 'End') next = el.tabs[el.tabs.length - 1];
        if (!next) return;
        e.preventDefault();
        next.focus();
        set({ type: next.dataset.type });
      });
    });

    let timer;
    el.q.addEventListener('input', () => {
      clearTimeout(timer);
      timer = setTimeout(() => set({ q: el.q.value }), 120);
    });
    el.q.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { clearTimeout(timer); set({ q: el.q.value }); }
    });

    el.cats.addEventListener('click', (e) => {
      const chip = e.target.closest('.chip');
      if (!chip) return;
      const c = chip.dataset.cat;
      set({ cat: state.cat.includes(c) ? state.cat.filter((x) => x !== c) : [...state.cat, c] });
    });
    el.state.addEventListener('change', () => set({ state: el.state.value }));
    el.days.addEventListener('change', () => set({ days: el.days.value }));
    el.noclear.addEventListener('change', () => set({ noclear: el.noclear.checked }));
    el.hideclosed.addEventListener('change', () => set({ closed: !el.hideclosed.checked }));
    el.sort.addEventListener('change', () => set({ sort: el.sort.value }));

    const reset = () => set({ ...DEFAULTS, type: state.type, sort: state.sort });
    $('reset').addEventListener('click', reset);
    $('empty-reset').addEventListener('click', reset);
    $('empty-jump').addEventListener('click', (e) => {
      const btn = e.target.closest('.jump');
      if (btn) set({ type: btn.dataset.type });
    });

    el.toggle.addEventListener('click', () => {
      const open = el.toggle.getAttribute('aria-expanded') !== 'true';
      el.toggle.setAttribute('aria-expanded', String(open));
      el.filters.classList.toggle('open', open);
    });

    window.addEventListener('popstate', () => { state = readUrl(); render(); });

    // "/" jumps to search, as on GitHub.
    document.addEventListener('keydown', (e) => {
      if (e.key !== '/' || e.target.closest('input, select, textarea')) return;
      e.preventDefault();
      el.q.focus();
    });
  }

  function prepare(row) {
    const states = row.states || [];
    row.slug = slug(row.category);
    row.age = daysAgo(row.date_added);
    row.hay = ` ${words([row.company, row.role, row.location, row.category,
      ...states.map((s) => STATE_NAMES[s] || ''), row.remote ? 'remote' : ''].join(' ')).join(' ')}`;
    row.states = states;
    return row;
  }

  async function start() {
    bind();
    try {
      const res = await fetch('listings.json', { cache: 'no-cache' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      categories = data.categories || [];
      newDays = data.new_days || 7;
      rows = (data.rows || []).filter((r) => TYPES.includes(r.type)).map(prepare);
      if (data.generated) el.updated.textContent = `updated ${shortDate(data.generated)}`;
    } catch (err) {
      el.count.innerHTML = `Could not load the roles (${esc(err.message)}). The <a href="${REPO}#readme">README tables</a> list the same roles.`;
      return;
    }
    buildControls();
    state = readUrl();
    render();
  }

  start();
})();
