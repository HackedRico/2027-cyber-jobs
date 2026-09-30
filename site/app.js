// The board page: fetches listings.json once, then filters in memory. Every
// filter lives in the query string so a filtered view can be shared. Applied
// marks and the theme are per-browser, so they live in localStorage instead.
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
  const KEYS = { applied: 'cj-applied', hideApplied: 'cj-hide-applied', theme: 'cj-theme' };
  const PULSE_DAYS = 30;
  const APPLIED_TAG = '<span class="tag tag-applied">Applied</span>';

  const ICON = {
    arrow: '<svg aria-hidden="true" viewBox="0 0 16 16" width="12" height="12"><path d="M5 3h8v8M13 3 3 13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    check: '<svg aria-hidden="true" viewBox="0 0 16 16" width="15" height="15"><path d="m3 8.5 3.2 3.2L13 5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    flag: '<svg aria-hidden="true" viewBox="0 0 16 16" width="14" height="14"><path d="M3.5 14.5V2.5M3.5 3h8.5l-1.8 3.2L12 9.4H3.5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    x: '<svg aria-hidden="true" viewBox="0 0 16 16" width="10" height="10"><path d="m4 4 8 8M12 4l-8 8" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
  };

  const $ = (id) => document.getElementById(id);
  const el = {
    tabs: [...document.querySelectorAll('[role="tab"]')],
    panel: $('panel'),
    q: $('q'),
    cats: $('cats'),
    state: $('state'),
    days: [...document.querySelectorAll('input[name="days"]')],
    noclear: $('noclear'),
    hideclosed: $('hideclosed'),
    hideapplied: $('hideapplied'),
    sort: $('sort'),
    count: $('result-count'),
    active: $('active'),
    rows: $('results'),
    empty: $('empty'),
    rail: $('rail'),
    filterPanel: $('filter-panel'),
    sheet: $('sheet'),
    sheetBody: $('sheet-body'),
    sheetShow: $('sheet-show'),
    toggle: $('filters-toggle'),
    badge: $('filter-badge'),
    alerts: $('alerts'),
    theme: $('theme'),
    updated: $('updated'),
    pulse: $('pulse'),
    pulsePlot: $('pulse-plot'),
  };

  let rows = [];
  let categories = [];
  let state = { ...DEFAULTS };
  let newDays = 7;
  let pulse = [];
  const applied = new Set(load(KEYS.applied, []));
  let hideApplied = load(KEYS.hideApplied, false) === true;
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

  function plural(n, one, many) { return `${n} ${n === 1 ? one : many}`; }

  // Storage can throw in a private window or with site data blocked; the page
  // then works as before, the marks just last for this visit.
  function load(key, fallback) {
    try {
      const raw = localStorage.getItem(key);
      return raw === null ? fallback : JSON.parse(raw);
    } catch {
      return fallback;
    }
  }

  function save(key, value) {
    try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* see load */ }
  }

  // ---- URL state ---------------------------------------------------------

  function readUrl() {
    const p = new URLSearchParams(location.search);
    const type = p.get('type');
    const slugs = new Set(categories.map(slug));
    // A shared ?state=WY link outlives the last Wyoming row. Kept, it would
    // filter to nothing while the dropdown reads "Anywhere in the US".
    const places = new Set([...el.state.options].map((o) => o.value));
    const place = p.get('state') || '';
    return {
      type: TYPES.includes(type) ? type : DEFAULTS.type,
      q: (p.get('q') || '').slice(0, 120),
      cat: (p.get('cat') || '').split(',').filter((c) => slugs.has(c)),
      state: places.has(place) ? place : '',
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

  function activeCount() {
    return state.cat.length + (state.state ? 1 : 0) + (state.days ? 1 : 0)
      + (state.noclear ? 1 : 0) + (state.closed ? 1 : 0) + (hideApplied ? 1 : 0);
  }

  function syncControls() {
    el.tabs.forEach((tab) => {
      const on = tab.dataset.type === state.type;
      tab.setAttribute('aria-selected', String(on));
      tab.tabIndex = on ? 0 : -1;
    });
    el.panel.setAttribute('aria-labelledby', `tab-${state.type}`);
    if (el.q.value !== state.q) el.q.value = state.q;
    el.cats.querySelectorAll('input').forEach((box) => {
      box.checked = state.cat.includes(box.value);
    });
    el.state.value = [...el.state.options].some((o) => o.value === state.state) ? state.state : '';
    el.days.forEach((radio) => { radio.checked = radio.value === state.days; });
    el.noclear.checked = state.noclear;
    el.hideclosed.checked = !state.closed;
    el.hideapplied.checked = hideApplied;
    el.sort.value = state.sort;
    const active = activeCount();
    el.badge.hidden = active === 0;
    el.badge.textContent = active;
  }

  // ---- Filtering ---------------------------------------------------------

  // Words, not substrings: "soc" must not match "Associate".
  function words(text) {
    return text.toLowerCase().normalize('NFKD').replace(/[̀-ͯ]/g, '')
      .split(/[^a-z0-9+#]+/).filter(Boolean);
  }

  function terms() { return words(state.q).map((w) => ` ${w}`); }

  // Every filter except category, so the category list can show what each
  // box would add.
  function passes(row, needles) {
    if (!state.closed && row.closed) return false;
    if (state.noclear && row.clearance) return false;
    if (hideApplied && applied.has(row.id)) return false;
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

  // Marks the same word starts the search matched. Safari before 16.4 has no
  // lookbehind, so there the rows render unmarked.
  function highlighter(needles) {
    const alts = [...new Set(needles.map((n) => n.trim()))]
      .sort((a, b) => b.length - a.length)
      .map((w) => w.replace(/[+#]/g, '\\$&'));
    if (!alts.length) return null;
    try {
      return new RegExp(`(?<![\\p{L}\\p{N}+#])(?:${alts.join('|')})`, 'giu');
    } catch {
      return null;
    }
  }

  function hl(text, re) {
    if (!re) return esc(text);
    let out = '';
    let last = 0;
    for (const m of text.matchAll(re)) {
      out += `${esc(text.slice(last, m.index))}<mark>${esc(m[0])}</mark>`;
      last = m.index + m[0].length;
    }
    return out + esc(text.slice(last));
  }

  // ---- Rendering ---------------------------------------------------------

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
    if (parts.length <= 1) {
      const text = loc || 'United States';
      return `<span class="loc" title="${esc(text)}">${esc(text)}</span>`;
    }
    const rest = parts.slice(1).map((p) => `<li>${esc(p)}</li>`).join('');
    return `<details class="locs"><summary><span class="loc">${esc(parts[0])}</span><span class="more">+${parts.length - 1}<span class="sr-only"> more locations</span></span></summary><ul>${rest}</ul></details>`;
  }

  function ageText(age, iso) {
    if (age <= 0) return 'Today';
    if (age === 1) return 'Yesterday';
    if (age < 7) return `${age}d ago`;
    return shortDate(iso);
  }

  function rowHtml(row, repeat, re) {
    const isNew = !row.closed && row.age <= newDays;
    const isApplied = applied.has(row.id);
    const cls = ['row', row.closed && 'is-closed', isApplied && 'is-applied', repeat && 'is-repeat']
      .filter(Boolean).join(' ');
    const label = esc(`${row.company}, ${row.role}`);
    const flag = row.clearance
      ? '<span class="tag tag-flag" title="Mentions a security clearance, public trust or U.S. citizenship"><span aria-hidden="true">🇺🇸</span> Clearance</span>'
      : '';
    const url = safeUrl(row.url);
    let action = '';
    if (row.closed) {
      action = `<span class="closed-note">Closed${row.closed_date ? ` ${esc(shortDate(row.closed_date))}` : ''}</span>`;
    } else if (url) {
      action = `<a class="apply" href="${esc(url)}" target="_blank" rel="noopener noreferrer">Apply<span class="sr-only">: ${label}</span>${ICON.arrow}</a>`;
    }
    return `<li class="${cls}" data-id="${esc(row.id)}">
<div class="c-top"><span class="company">${hl(row.company, re)}</span><span class="cat">${hl(row.category, re)}</span>${flag}${isApplied ? APPLIED_TAG : ''}</div>
<div class="c-role"><span class="role">${hl(row.role, re)}</span></div>
<div class="c-loc">${locationHtml(row.location)}</div>
<div class="c-date"><time class="age${isNew ? ' is-new' : ''}" datetime="${esc(row.date_added)}" title="Added ${esc(row.date_added)}">${esc(ageText(row.age, row.date_added))}${isNew ? '<span class="sr-only">, new</span>' : ''}</time></div>
<div class="c-act">${action}<button type="button" class="mark" aria-pressed="${isApplied}" aria-label="Applied: ${label}" title="${isApplied ? 'Unmark as applied' : 'Mark as applied'}">${ICON.check}</button></div>
<div class="c-rep"><a class="report" href="${esc(reportUrl(row))}" target="_blank" rel="noopener noreferrer" title="Report this row">${ICON.flag}<span class="sr-only">Report ${label}</span></a></div>
</li>`;
  }

  function catName(s) { return categories.find((c) => slug(c) === s) || s; }

  function pills() {
    const out = [];
    const q = state.q.trim();
    if (q) out.push(['q', `"${q}"`]);
    for (const c of state.cat) out.push([`cat:${c}`, catName(c)]);
    if (state.state) out.push(['state', state.state === 'remote' ? 'Remote (US)' : STATE_NAMES[state.state] || state.state]);
    if (state.days) out.push(['days', `Added in ${state.days} days`]);
    if (state.noclear) out.push(['noclear', 'No 🇺🇸 clearance']);
    if (state.closed) out.push(['closed', 'Closed roles shown']);
    if (hideApplied) out.push(['applied', 'Applied roles hidden']);
    const html = out.map(([key, text]) => (
      `<button type="button" class="pill" data-clear="${esc(key)}" aria-label="Remove filter: ${esc(text)}">${esc(text)}${ICON.x}</button>`
    ));
    if (out.length > 1) html.push('<button type="button" class="pill pill-clear" data-clear="all">Clear all</button>');
    return html.join('');
  }

  function describe(n, type) {
    const [one, many] = TYPE_NOUNS[type];
    return `<strong>${n}</strong> ${n === 1 ? one : many}`;
  }

  let firstPaint = true;

  function render() {
    const needles = terms();
    const re = highlighter(needles);
    const catSet = new Set(state.cat);
    const counts = { intern: 0, newgrad: 0, earlycareer: 0 };
    const facets = Object.create(null);
    const shown = [];
    for (const row of rows) {
      if (!passes(row, needles)) continue;
      if (row.type === state.type) facets[row.slug] = (facets[row.slug] || 0) + 1;
      if (catSet.size && !catSet.has(row.slug)) continue;
      counts[row.type] += 1;
      if (row.type === state.type) shown.push(row);
    }
    shown.sort(state.sort === 'company' ? byCompany : byDate);

    document.querySelectorAll('[data-count]').forEach((node) => {
      node.textContent = counts[node.dataset.count];
    });
    el.tabs.forEach((tab) => {
      const t = tab.dataset.type;
      tab.setAttribute('aria-label', `${tab.querySelector('.tab-label').textContent}, ${plural(counts[t], 'role', 'roles')}`);
    });
    el.cats.querySelectorAll('.facet').forEach((item) => {
      const n = facets[item.dataset.cat] || 0;
      item.querySelector('.facet-n').textContent = n;
      item.classList.toggle('is-empty', n === 0);
    });

    const total = rows.filter((r) => r.type === state.type && (state.closed || !r.closed)).length;
    el.count.innerHTML = shown.length !== total
      ? `${describe(shown.length, state.type)} of ${total}`
      : describe(shown.length, state.type);
    el.active.innerHTML = pills();
    el.sheetShow.textContent = `Show ${plural(shown.length, 'role', 'roles')}`;

    el.rows.classList.toggle('is-first', firstPaint);
    el.rows.innerHTML = shown.map((row, i) => rowHtml(row, i > 0 && shown[i - 1].company === row.company, re)).join('');
    if (firstPaint) {
      [...el.rows.children].slice(0, 14).forEach((li, i) => { li.style.animationDelay = `${i * 24}ms`; });
      firstPaint = false;
    }
    el.empty.hidden = shown.length !== 0;
    if (!shown.length) {
      const others = TYPES.filter((t) => t !== state.type && counts[t]);
      $('empty-hint').textContent = others.length ? 'Other tabs have matches:' : 'Try fewer filters.';
      $('empty-jump').innerHTML = others.map((t) => (
        `<button type="button" class="btn btn-primary jump" data-type="${t}">Show ${counts[t]} ${TYPE_NOUNS[t][counts[t] === 1 ? 0 : 1]}</button>`
      )).join('');
    }
    syncControls();
    writeUrl();
  }

  // ---- Summary strip -----------------------------------------------------

  function renderSummary(generated) {
    const open = rows.filter((r) => !r.closed);
    $('stat-open').textContent = open.length.toLocaleString('en-US');
    $('stat-new').textContent = open.filter((r) => r.age <= newDays).length.toLocaleString('en-US');
    $('stat-cos').textContent = new Set(open.map((r) => r.company)).size.toLocaleString('en-US');
    if (generated) el.updated.textContent = `Updated ${shortDate(generated)}`;
    $('status-more').textContent = ` · ${open.length} open · ${$('stat-new').textContent} new`;

    pulse = Array.from({ length: PULSE_DAYS }, () => 0);
    for (const r of open) {
      if (r.age >= 0 && r.age < PULSE_DAYS) pulse[PULSE_DAYS - 1 - r.age] += 1;
    }
    const start = new Date(today);
    start.setDate(start.getDate() - (PULSE_DAYS - 1));
    $('pulse-start').textContent = `${MONTHS[start.getMonth()]} ${start.getDate()}`;
    el.pulse.hidden = false;
    drawPulse();
    if ('ResizeObserver' in window) new ResizeObserver(drawPulse).observe(el.pulsePlot);
  }

  function dayLabel(i) {
    const d = new Date(today);
    d.setDate(d.getDate() - (PULSE_DAYS - 1 - i));
    return `${MONTHS[d.getMonth()]} ${d.getDate()}`;
  }

  function barPath(x, y, w, h) {
    const r = Math.min(3, w / 2, h);
    return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`;
  }

  let drawnWidth = 0;

  // Open rows only: the page keeps closed rows for 14 days, so counting them
  // would make recent days look busier than older ones.
  function drawPulse() {
    const w = Math.floor(el.pulsePlot.clientWidth);
    if (!w || w === drawnWidth) return;
    const animate = drawnWidth === 0;
    drawnWidth = w;
    const h = 44;
    const room = 12;
    const slot = w / PULSE_DAYS;
    const bw = Math.max(2, Math.min(24, slot - 2));
    const max = Math.max(1, ...pulse);
    const peak = pulse.lastIndexOf(max);
    const bars = pulse.map((n, i) => {
      if (!n) return '';
      const bh = Math.max(2, Math.round((n / max) * (h - room)));
      const x = i * slot + (slot - bw) / 2;
      const recent = PULSE_DAYS - 1 - i <= newDays;
      const delay = animate ? ` style="animation-delay:${i * 14}ms"` : ' style="animation:none"';
      return `<path class="bar${recent ? ' is-new' : ''}" data-i="${i}" d="${barPath(x, h - bh, bw, bh)}"${delay}/>`;
    }).join('');
    const px = peak * slot + slot / 2;
    const anchor = px > w - 12 ? 'end' : 'middle';
    const total = pulse.reduce((a, b) => a + b, 0);
    const summary = `Open roles by day added over the last ${PULSE_DAYS} days: ${total} in all, most on ${dayLabel(peak)} with ${max}.`;
    el.pulsePlot.innerHTML = `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" role="img" aria-label="${esc(summary)}">`
      + `<line class="base" x1="0" x2="${w}" y1="${h - 0.5}" y2="${h - 0.5}"/>${bars}`
      + `<text class="peak" x="${anchor === 'end' ? w : px}" y="${h - Math.round(h - room) - 3}" text-anchor="${anchor}">${max}</text>`
      + '</svg><div class="tip" hidden></div>';
  }

  function bindPulse() {
    const plot = el.pulsePlot;
    plot.addEventListener('pointermove', (e) => {
      const svg = plot.querySelector('svg');
      if (!svg) return;
      const box = svg.getBoundingClientRect();
      const slot = box.width / PULSE_DAYS;
      const i = Math.min(PULSE_DAYS - 1, Math.max(0, Math.floor((e.clientX - box.left) / slot)));
      const tip = plot.querySelector('.tip');
      tip.textContent = `${dayLabel(i)}: ${plural(pulse[i], 'open role', 'open roles')}`;
      tip.hidden = false;
      const half = tip.offsetWidth / 2;
      tip.style.left = `${Math.min(box.width - half, Math.max(half, i * slot + slot / 2))}px`;
      plot.classList.add('is-hover');
      plot.querySelectorAll('.bar').forEach((bar) => bar.classList.toggle('is-hot', Number(bar.dataset.i) === i));
    });
    plot.addEventListener('pointerleave', () => {
      plot.classList.remove('is-hover');
      const tip = plot.querySelector('.tip');
      if (tip) tip.hidden = true;
    });
  }

  // ---- Setup -------------------------------------------------------------

  function buildControls() {
    const present = new Set(rows.map((r) => r.category));
    el.cats.innerHTML = categories.filter((c) => present.has(c)).map((c) => (
      `<label class="facet" data-cat="${esc(slug(c))}"><input type="checkbox" value="${esc(slug(c))}"><span class="facet-name">${esc(c)}</span><span class="facet-n">0</span></label>`
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

  function setHideApplied(on) {
    hideApplied = on;
    save(KEYS.hideApplied, on);
  }

  function reset() {
    setHideApplied(false);
    set({ ...DEFAULTS, type: state.type, sort: state.sort });
  }

  function clearFilter(key) {
    if (key === 'all') return reset();
    if (key === 'applied') { setHideApplied(false); return render(); }
    if (key.startsWith('cat:')) return set({ cat: state.cat.filter((c) => c !== key.slice(4)) });
    return set({ [key]: DEFAULTS[key] });
  }

  function toggleApplied(button) {
    const li = button.closest('.row');
    const id = li.dataset.id;
    if (applied.has(id)) applied.delete(id); else applied.add(id);
    save(KEYS.applied, [...applied]);
    if (hideApplied) {
      render();
      el.rows.focus();
      return;
    }
    // Patched in place so focus stays on the button.
    const on = applied.has(id);
    li.classList.toggle('is-applied', on);
    button.setAttribute('aria-pressed', String(on));
    button.title = on ? 'Unmark as applied' : 'Mark as applied';
    const top = li.querySelector('.c-top');
    const tag = top.querySelector('.tag-applied');
    if (on && !tag) top.insertAdjacentHTML('beforeend', APPLIED_TAG);
    if (!on && tag) tag.remove();
  }

  // ---- Dialogs -----------------------------------------------------------

  const desktop = matchMedia('(min-width: 1100px)');

  function openSheet() {
    el.sheetBody.append(el.filterPanel);
    el.sheet.showModal();
    el.toggle.setAttribute('aria-expanded', 'true');
  }

  function openAlerts() {
    if (el.alerts.open) return;
    el.alerts.showModal();
    if (location.hash !== '#alerts') history.replaceState(null, '', `${location.pathname}${location.search}#alerts`);
  }

  function bindDialogs() {
    for (const dialog of [el.sheet, el.alerts]) {
      // The card fills the dialog, so a click that lands on the dialog itself
      // came through the backdrop.
      dialog.addEventListener('click', (e) => {
        if (e.target === dialog || e.target.closest('[data-close]')) dialog.close();
      });
    }
    el.sheet.addEventListener('close', () => {
      el.rail.append(el.filterPanel);
      el.toggle.setAttribute('aria-expanded', 'false');
    });
    desktop.addEventListener('change', (e) => { if (e.matches && el.sheet.open) el.sheet.close(); });
    el.toggle.addEventListener('click', openSheet);
    $('sheet-reset').addEventListener('click', reset);

    document.querySelectorAll('[data-alerts]').forEach((link) => {
      link.addEventListener('click', (e) => { e.preventDefault(); openAlerts(); });
    });
    el.alerts.addEventListener('close', () => {
      if (location.hash === '#alerts') history.replaceState(null, '', location.pathname + location.search);
    });
    window.addEventListener('hashchange', () => { if (location.hash === '#alerts') openAlerts(); });

    el.alerts.addEventListener('click', async (e) => {
      const btn = e.target.closest('.copy');
      if (!btn) return;
      const url = new URL(btn.dataset.copy, location.href).href;
      try {
        await navigator.clipboard.writeText(url);
        btn.textContent = 'Copied';
      } catch {
        // No clipboard on plain http or without permission: show the URL so
        // it can be copied by hand.
        btn.textContent = url;
      }
      btn.classList.add('is-done');
      setTimeout(() => { btn.textContent = 'Copy URL'; btn.classList.remove('is-done'); }, 1800);
    });
  }

  // ---- Theme -------------------------------------------------------------

  const systemDark = matchMedia('(prefers-color-scheme: dark)');

  function isDark() {
    const chosen = document.documentElement.dataset.theme;
    return chosen ? chosen === 'dark' : systemDark.matches;
  }

  function syncTheme() {
    const dark = isDark();
    el.theme.classList.toggle('is-dark', dark);
    el.theme.setAttribute('aria-label', dark ? 'Switch to light theme' : 'Switch to dark theme');
    const bar = getComputedStyle(document.documentElement).getPropertyValue('--panel').trim();
    document.querySelectorAll('meta[name="theme-color"]').forEach((m) => m.setAttribute('content', bar));
  }

  function bindTheme() {
    el.theme.addEventListener('click', () => {
      const next = isDark() ? 'light' : 'dark';
      document.documentElement.dataset.theme = next;
      try { localStorage.setItem(KEYS.theme, next); } catch { /* see load */ }
      syncTheme();
    });
    systemDark.addEventListener('change', syncTheme);
    syncTheme();
  }

  // ---- Events ------------------------------------------------------------

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

    el.cats.addEventListener('change', (e) => {
      const c = e.target.value;
      set({ cat: e.target.checked ? [...state.cat, c] : state.cat.filter((x) => x !== c) });
    });
    el.state.addEventListener('change', () => set({ state: el.state.value }));
    el.days.forEach((radio) => radio.addEventListener('change', () => set({ days: radio.value })));
    el.noclear.addEventListener('change', () => set({ noclear: el.noclear.checked }));
    el.hideclosed.addEventListener('change', () => set({ closed: !el.hideclosed.checked }));
    el.hideapplied.addEventListener('change', () => { setHideApplied(el.hideapplied.checked); render(); });
    el.sort.addEventListener('change', () => set({ sort: el.sort.value }));

    $('reset').addEventListener('click', reset);
    $('empty-reset').addEventListener('click', reset);
    $('empty-jump').addEventListener('click', (e) => {
      const btn = e.target.closest('.jump');
      if (btn) set({ type: btn.dataset.type });
    });
    el.active.addEventListener('click', (e) => {
      const pill = e.target.closest('[data-clear]');
      if (pill) clearFilter(pill.dataset.clear);
    });
    el.rows.addEventListener('click', (e) => {
      const btn = e.target.closest('.mark');
      if (btn) toggleApplied(btn);
    });

    window.addEventListener('popstate', () => { state = readUrl(); render(); });

    // "/" jumps to search, as on GitHub.
    document.addEventListener('keydown', (e) => {
      if (e.key !== '/' || e.target.closest('input, select, textarea') || document.querySelector('dialog[open]')) return;
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
    bindTheme();
    bindDialogs();
    bindPulse();
    bind();
    if (location.hash === '#alerts') openAlerts();
    let data;
    try {
      const res = await fetch('listings.json', { cache: 'no-cache' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      data = await res.json();
      categories = data.categories || [];
      newDays = data.new_days || 7;
      rows = (data.rows || []).filter((r) => TYPES.includes(r.type)).map(prepare);
    } catch (err) {
      el.rows.innerHTML = '';
      el.count.innerHTML = `Could not load the roles (${esc(err.message)}). The <a href="${REPO}#readme">README tables</a> list the same roles.`;
      return;
    }
    buildControls();
    state = readUrl();
    render();
    renderSummary(data.generated);
  }

  start();
})();
