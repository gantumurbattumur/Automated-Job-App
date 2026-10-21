/* JobPrep UI — plain JS, no build step. Talks to the local FastAPI backend. */
'use strict';

// ---------- helpers ----------
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
async function api(url, opts = {}) {
  const r = await fetch(url, opts);
  if (!r.ok) { let d = r.statusText; try { d = (await r.json()).detail || d; } catch (e) {} throw new Error(d); }
  return r.headers.get('content-type')?.includes('json') ? r.json() : r.text();
}
const send = (method, url, body) => api(url, { method, headers: { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) });
const put = (u, b) => send('PUT', u, b), post = (u, b) => send('POST', u, b ?? {}), del = u => send('DELETE', u);
const enc = encodeURIComponent;
function toast(msg) { const t = document.createElement('div'); t.className = 'toast'; t.textContent = msg; document.body.append(t); setTimeout(() => t.remove(), 2200); }
function ago(d) {
  if (!d) return '—'; const h = (Date.now() - new Date(d)) / 36e5;
  return h < 1 ? 'just now' : h < 48 ? `${Math.round(h)}h ago` : `${Math.round(h / 24)}d ago`;
}
const day = d => d ? new Date(d).toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) : '—';
// deadlines are calendar dates stored as midnight UTC: show them in UTC so "Dec 1" doesn't become "Nov 30" in California
const dateOnly = d => d ? new Date(d).toLocaleDateString(undefined, { month: 'short', day: 'numeric', timeZone: 'UTC' }) : '—';
const scoreCls = s => s >= 65 ? 'hi' : s >= 45 ? 'mid' : '';
const store = { get(k, d) { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch (e) { return d; } },
                set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} } };
const copyText = (text, label = 'Copied') => navigator.clipboard.writeText(text).then(() => toast(label));
document.addEventListener('click', e => { const c = e.target.closest('[data-copy]'); if (c) copyText(c.dataset.copy, `Copied: ${c.dataset.copy.slice(0, 40)}`); });

// ---------- vocab ----------
const CAT = { ai_ml: 'AI/ML', data_science: 'Data science', data_eng: 'Data eng', swe: 'Software', research: 'Research', quant: 'Quant',
  hardware: 'Hardware', product: 'Product', design: 'Design', other: 'Other' };
const STATUSES = ['saved', 'opened', 'applied', 'oa', 'interview', 'offer', 'rejected', 'withdrawn', 'ghosted', 'dismissed'];
const SLABEL = { saved: 'Saved', opened: 'Opened', applied: 'Applied', oa: 'Online assessment', interview: 'Interview', offer: 'Offer',
  rejected: 'Rejected', withdrawn: 'Withdrawn', ghosted: 'Ghosted', dismissed: 'Not interested' };
const CPT = { friendly: ['ok', 'CPT friendly'], unclear: ['warn', 'CPT unclear'], blocked: ['bad', 'No CPT'] };
const SRC = { simplify: 'SimplifyJobs', greenhouse: 'Greenhouse', lever: 'Lever', ashby: 'Ashby', workday: 'Workday',
  smartrecruiters: 'SmartRecruiters', workable: 'Workable', manual: 'Manual' };
const PARTS = { relevance: ['Resume match', '--c-rel'], freshness: ['Freshness', '--c-fresh'], friendliness: ['CPT-friendliness', '--c-cpt'],
  personal: ['Your taste', '--c-pers'], allowlist: ['Allowlist boost', '--c-boost'] };
const yr = new Date().getFullYear();
const SEASONS = [`fall ${yr}`, `winter ${yr + 1}`, `spring ${yr + 1}`, `summer ${yr + 1}`, `fall ${yr + 1}`, `summer ${yr + 2}`];

// filter panel: [group, open by default, fields]. types: multi | bool | list | num | select | range
const FILTERS = [
  ['Time', true, [
    { k: 'posted_within_days', t: 'select', l: 'Posted within', num: 1, o: { 0: 'Any time', 1: 'Last 24 hours', 3: 'Last 3 days', 7: 'Last 7 days', 14: 'Last 14 days', 30: 'Last 30 days' } },
    { k: 'hide_expired', t: 'bool', l: 'Hide postings whose deadline passed' },
    { k: 'hide_past_seasons', t: 'bool', l: `Hide past seasons (e.g. Summer ${yr})` }]],
  ['Role', true, [
    { k: 'categories', t: 'multi', l: 'Category', o: CAT, h: 'None selected = any' },
    { k: 'seasons', t: 'multi', l: 'Season', o: Object.fromEntries(SEASONS.map(s => [s, s.replace(/^\w/, c => c.toUpperCase())])), h: 'Postings without a season are kept' },
    { k: 'job_types', t: 'multi', l: 'Type', o: { internship: 'Internship', coop: 'Co-op' } },
    { k: 'title_include', t: 'list', l: 'Title must include any of', h: 'Comma separated' },
    { k: 'title_exclude', t: 'list', l: 'Title must not include' },
    { k: 'hide_education', t: 'multi', l: 'Hide eligibility', o: { undergrad_only: 'Undergrad only', masters_not_listed: "Master's not listed", phd: 'PhD only', mba: 'MBA', high_school: 'High school' } }]],
  ['Work authorization (F-1 CPT)', true, [
    { k: 'cpt_allow', t: 'multi', l: 'Show CPT stance', o: { friendly: 'Says OK', unclear: 'Unclear', blocked: 'No CPT' } },
    { k: 'hide_auth', t: 'multi', l: 'Hide if it requires', o: { citizenship: 'US citizenship', green_card: 'Green card', clearance: 'Clearance', export_control: 'ITAR / US person' } },
    { k: 'no_future_sponsorship', t: 'select', l: '"No sponsorship later" postings', o: { warn: 'Show with a warning', hide: 'Hide', ignore: 'Ignore' } },
    { k: 'only_h1b_sponsors', t: 'bool', l: 'Only companies with H-1B history' },
    { k: 'h1b_min_approvals', t: 'num', l: 'H-1B approvals that count as a sponsor', h: 'USCIS data, FY2025–26' }]],
  ['Company', false, [
    { k: 'exclude_space', t: 'bool', l: 'Exclude space, aerospace & defense' },
    { k: 'company_block', t: 'list', l: 'Never show' },
    { k: 'company_allow', t: 'list', l: 'Favorites (boosted, never blocked)' },
    { k: 'hide_applied', t: 'bool', l: 'Hide jobs already in my tracker' },
    { k: 'hide_applied_companies', t: 'bool', l: 'Hide companies I already applied to' },
    { k: 'cpt_issue_companies', t: 'select', l: 'Companies that rejected me over CPT', o: { demote: 'Rank lower + warn', hide: 'Hide', ignore: 'Ignore' } }]],
  ['Location', false, [
    { k: 'us_only', t: 'bool', l: 'United States only' },
    { k: 'work_modes', t: 'multi', l: 'Work mode', o: { remote: 'Remote', hybrid: 'Hybrid', onsite: 'On-site' } },
    { k: 'locations_include', t: 'list', l: 'Cities / states (any of)', h: 'Remote jobs always pass' }]],
  ['Pay', false, [
    { k: 'min_hourly', t: 'num', l: 'Minimum $/hour', h: 'Only applies when a posting lists pay' },
    { k: 'hide_unpaid', t: 'bool', l: 'Hide unpaid' }]],
  ['Match', false, [
    { k: 'lane', t: 'select', l: 'Resume to match against', o: {} },
    { k: 'min_score', t: 'num', l: 'Minimum score (0–100)' },
    { k: 'min_relevance', t: 'num', l: 'Minimum resume match %' },
    { k: 'skills_required', t: 'list', l: 'Must mention any of' },
    { k: 'skills_excluded', t: 'list', l: 'Hide if it mentions' }]],
  ['Ranking', false, [
    { k: 'weights.relevance', t: 'range', l: 'Resume match' },
    { k: 'weights.freshness', t: 'range', l: 'Freshness' },
    { k: 'weights.friendliness', t: 'range', l: 'CPT-friendliness' },
    { k: 'weights.personal', t: 'range', l: 'Your taste (learned)' },
    { k: 'freshness_half_life_days', t: 'num', l: 'Freshness half-life (days)', h: 'A posting loses half its freshness every N days' },
    { k: 'h1b_boost', t: 'bool', l: 'Treat H-1B sponsors as likely CPT-friendly' },
    { k: 'allow_boost', t: 'num', l: 'Favorites boost (points)' },
    { k: 'new_badge_hours', t: 'num', l: '"NEW" badge for (hours)' },
    { k: 'stale_days', t: 'num', l: 'Warn when open longer than (days)', h: '0 = off' }]],
];
const SETTINGS_GROUPS = [
  ['Schedule', 'When JobPrep fetches postings, scans Gmail and tidies up. “Once a day” does everything in one morning run (~5 min, ~0.2 GB) so your list is ready when you sit down.', [
    { k: 'schedule', t: 'select', l: 'Run', o: { daily: 'Once a day (recommended)', interval: 'Every few minutes (uses much more data)' } },
    { k: 'daily_time', t: 'time', l: 'Morning run at (local time)', h: 'If your Mac is asleep then, it runs as soon as it wakes up.' },
    { k: 'refresh_simplify_minutes', t: 'num', l: 'SimplifyJobs list every (minutes)', h: 'Only in “every few minutes” mode. 0 = off', mode: 'interval' },
    { k: 'refresh_ats_minutes', t: 'num', l: 'Company job boards every (minutes)', h: 'Only in “every few minutes” mode. ~970 boards, ~200 MB per run', mode: 'interval' },
    { k: 'gmail_scan_minutes', t: 'num', l: 'Gmail scan every (minutes)', h: 'Only in “every few minutes” mode', mode: 'interval' }]],
  ['Alerts', 'Mac notifications.', [
    { k: 'digest', t: 'bool', l: 'Notify me when the morning list is ready (top 10 new matches)' },
    { k: 'notify', t: 'bool', l: 'Also notify about each strong new match during a refresh', mode: 'interval' },
    { k: 'notify_min_score', t: 'num', l: 'Only when score is at least', h: 'Only in “every few minutes” mode; daily mode sends one summary', mode: 'interval' },
    { k: 'digest_hour', t: 'num', l: 'Digest hour (0–23)', h: 'Only in “every few minutes” mode; daily mode sends it right after the morning run', mode: 'interval' }]],
  ['Maintenance', 'Keeps the local database small and backed up.', [
    { k: 'prune_closed_days', t: 'num', l: 'Delete closed postings after (days)', h: 'Jobs in your tracker are never deleted. 0 = keep forever' },
    { k: 'weekly_backup', t: 'bool', l: 'Weekly backup of the database (keeps 4)' }]],
];
const FIELDS = Object.fromEntries([...FILTERS, ...SETTINGS_GROUPS].flatMap(g => g[2]).map(f => [f.k, f]));

// ---------- state ----------
let LANE_LABEL = {};
const laneLabel = l => LANE_LABEL[l] || String(l || '').replace('_', '/').toUpperCase();
let S = {}, DEFAULTS = {}, presets = {}, feed = { shown: [], hidden: [] }, selected = null, detailCache = {};
let q = '', sort = store.get('sort', 'score'), showHidden = false, pending = null;
let lastVisit = store.get('lastVisit', 0) || Date.now(); store.set('lastVisit', lastVisit);
const getk = k => k.split('.').reduce((o, p) => o?.[p], S);
const setk = (k, v) => { const p = k.split('.'); p.slice(0, -1).reduce((o, x) => o[x], S)[p.at(-1)] = v; };
const isUnseen = j => new Date(j.first_seen) > lastVisit;

// ---------- router ----------
const PAGES = { home: renderHome, jobs: renderJobsPage, tracker: renderTracker, inbox: renderInbox, resumes: renderResumes, profile: renderProfile, settings: renderSettings };
function go(page) {
  page = PAGES[page] ? page : 'home';
  for (const p in PAGES) $(`#page-${p}`).classList.toggle('hidden', p !== page);
  $$('#nav [data-page]').forEach(b => b.classList.toggle('on', b.dataset.page === page));
  if (location.hash !== `#${page}`) history.replaceState(null, '', `#${page}`);
  PAGES[page]().catch(e => toast(`Error: ${e.message}`));
}
$$('#nav [data-page]').forEach(b => b.onclick = () => go(b.dataset.page));
window.addEventListener('hashchange', () => go(location.hash.slice(1)));

// ---------- top bar: source status + refresh ----------
async function loadStatus() {
  const st = await api('/status');
  const sch = st.schedule, m = st.morning;
  const chip = sch?.mode === 'daily' ? (m?.running ? '<span class="tag ac"><span class="spin"></span> Morning run in progress</span>'
    : `<span class="tag ${sch.done_today ? 'ok' : ''}" title="Everything is fetched once a day; change it in Settings">${sch.done_today ? `✓ Today’s list ready${m?.last_run ? ` (${new Date(m.last_run).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })})` : ''}` : `Next run ${sch.daily_time}`}</span>`) : '';
  $('#srcStatus').innerHTML = chip + [['simplify', 'SimplifyJobs'], ['ats', 'Company boards']].map(([k, l]) => {
    const v = st[k]; if (!v) return `<span class="tag">${l}: not yet</span>`;
    if (v.running) return `<span class="tag ac"><span class="spin"></span> ${l}: refreshing</span>`;
    return `<span class="tag ${v.error ? 'bad' : ''}" title="${esc(v.error || `+${v.added} new · ${v.closed} closed · ${v.enriched} descriptions filled${v.boards ? ` · ${v.boards} boards` : ''}`)}">${l}: ${ago(v.last_run)}${v.added ? ` · +${v.added}` : ''}</span>`;
  }).join('');
  return st;
}
$('#refreshBtn').onclick = async () => {
  const r = await post('/refresh');
  $('#refreshMsg').textContent = r.started ? 'Refreshing in the background…' : 'Already refreshing…';
  const poll = setInterval(async () => {
    const st = await loadStatus();
    if (!['simplify', 'ats'].some(k => st[k]?.running)) { clearInterval(poll); $('#refreshMsg').textContent = 'Up to date'; detailCache = {}; if (!$('#page-jobs').classList.contains('hidden')) loadJobs(); }
  }, 4000);
};
async function loadInboxCount() {
  const items = await api('/inbox'); const el = $('#inboxCount');
  el.textContent = items.length; el.classList.toggle('hidden', !items.length);
}

// ---------- HOME ----------
async function renderHome() {
  const h = await api('/home');
  const s = h.stats, by = s.by_status;
  const pipe = ['saved', 'opened', 'applied', 'oa', 'interview', 'offer'];
  const p = h.personal;
  $('#page-home').innerHTML = `
    <div class="page-head"><div><h1>Good ${new Date().getHours() < 12 ? 'morning' : new Date().getHours() < 18 ? 'afternoon' : 'evening'}</h1>
      <p>Freshest, best-matching CPT-eligible internships first. Everything runs locally on this Mac.</p></div>
      <button class="btn primary" data-go="jobs">Browse ${h.shown.toLocaleString()} matches →</button></div>
    <div class="kpis">
      ${kpi(h.new_today, 'New in the last 24h')}${kpi(h.shown.toLocaleString(), 'Match your filters')}${kpi(h.cpt_friendly.toLocaleString(), 'CPT-friendly or H-1B sponsors')}
      ${kpi(s.applied_last_7d, 'Applied this week')}${kpi(`${Math.round(s.response_rate * 100)}%`, 'Response rate')}${kpi(by.interview + by.offer, 'Interviews & offers')}
    </div>
    <div class="home-grid">
      <div class="stack">
        <div class="card"><div class="hd"><h2>Today’s top matches</h2><span class="small mut">new in the last 24 hours, best first</span></div>
          <div>${h.top.length ? h.top.map(jobRow).join('') : '<div class="empty">Nothing new yet today — the next refresh is automatic.</div>'}</div></div>
        ${h.closing_soon.length ? `<div class="card"><div class="hd"><h2>Closing within a week</h2><span class="small mut">from deadlines stated in postings</span></div>
          <div>${h.closing_soon.map(jobRow).join('')}</div></div>` : ''}
      </div>
      <div class="stack">
        <div class="card"><div class="hd"><h2>Pipeline</h2><a href="#tracker">Open tracker</a></div><div class="bd">
          <div class="pipeline">${pipe.map(k => `<div><b>${by[k]}</b><span>${SLABEL[k]}</span></div>`).join('')}</div>
          <p class="small mut" style="margin:10px 0 0">${by.rejected} rejected · ${by.ghosted} ghosted (no news in 30 days) · ${s.applied_total} applied in total</p></div></div>
        <div class="card"><div class="hd"><h2>To do</h2></div><div>
          ${h.follow_ups.map(a => `<div class="list-row" data-go="tracker"><span class="tag warn">Follow up</span><span class="grow">${esc(a.company)} — ${esc(a.title)}</span><span class="small mut">${esc(a.follow_up)}</span></div>`).join('')}
          ${h.inbox ? `<div class="list-row" data-go="inbox"><span class="tag ac">Inbox</span><span class="grow">${h.inbox} job email${h.inbox > 1 ? 's' : ''} to review</span></div>` : ''}
          ${!h.gmail ? `<div class="list-row" data-go="inbox"><span class="tag">Setup</span><span class="grow">Connect Gmail so replies update your tracker automatically</span></div>` : ''}
          ${!h.follow_ups.length && !h.inbox && h.gmail ? '<div class="empty">All caught up.</div>' : ''}
        </div></div>
        <div class="card"><div class="hd"><h2>What JobPrep learned about you</h2></div><div class="bd small">
          ${p.active ? `<p style="margin:0 0 6px">Trained on ${p.positive} jobs you saved/applied to and ${p.negative} you marked not interested.</p>
            ${p.likes?.length ? `<div class="tags">${p.likes.map(x => `<span class="tag ok">likes ${esc(x)}</span>`).join('')}</div>` : ''}
            ${p.dislikes?.length ? `<div class="tags" style="margin-top:4px">${p.dislikes.map(x => `<span class="tag bad">avoids ${esc(x)}</span>`).join('')}</div>` : ''}`
            : `<p style="margin:0">Not trained yet: needs ${p.needed ?? 5} jobs you saved or applied to (${p.positive ?? 0} so far) and ${p.needed ?? 5} you marked “Not interested” (${p.negative ?? 0} so far). Until then, ranking ignores this part.</p>`}
        </div></div>
        <div class="card"><div class="hd"><h2>Sources</h2></div><div class="bd small">${sourceList(h.sources)}</div></div>
      </div>
    </div>`;
  bindRows($('#page-home'));
}
const kpi = (v, l) => `<div class="card kpi"><div class="v">${v}</div><div class="l">${l}</div></div>`;
function sourceList(st) {
  return [['morning', 'Morning run (everything)'], ['simplify', 'SimplifyJobs internship list'], ['ats', 'Company job boards'], ['gmail', 'Gmail scan'], ['digest', 'Morning digest'], ['maintenance', 'Cleanup & backup']]
    .map(([k, l]) => { const v = st[k]; return `<div class="row between" style="padding:3px 0"><span>${l}</span><span class="${v?.error ? 'bad' : 'mut'}">${v ? (v.running ? 'running…' : `${ago(v.last_run)}${v.error ? ' · error' : ''}`) : 'not yet'}</span></div>`; }).join('');
}
function jobRow(j) {
  return `<div class="list-row" data-open="${esc(j.id)}"><span class="score ${scoreCls(j.score)}">${Math.round(j.score)}</span>
    <span class="grow"><b>${esc(j.title)}</b><br><span class="small mut">${esc(j.company)} · ${esc(shortLoc(j.location))} · posted ${ago(j.posted_at)}</span></span>
    ${cptTag(j)}${j.days_left != null && j.days_left >= 0 ? `<span class="tag warn">${Math.ceil(j.days_left)}d left</span>` : ''}</div>`;
}
function bindRows(root) {
  $$('[data-go]', root).forEach(b => b.onclick = () => go(b.dataset.go));
  $$('[data-open]', root).forEach(b => b.onclick = () => { selected = b.dataset.open; go('jobs'); });
}
const shortLoc = l => { const parts = String(l || '').split(';').map(x => x.trim()).filter(Boolean); return parts.length > 1 ? `${parts[0]} +${parts.length - 1}` : (parts[0] || 'Location not listed'); };
function cptTag(j) {
  if (j.cpt_label?.startsWith('likely')) return `<span class="tag ok" title="${esc(j.cpt_label)}">Likely CPT OK</span>`;
  if (j.cpt_label?.startsWith('rejected')) return `<span class="tag bad">Rejected you over CPT</span>`;
  const [c, l] = CPT[j.cpt] || ['', j.cpt]; return `<span class="tag ${c}" title="${esc(j.cpt_label || '')}">${l}</span>`;
}

// ---------- JOBS ----------
async function renderJobsPage() {
  if (!Object.keys(S).length) await loadSettings();
  renderFilters();
  await loadJobs();
}
async function loadSettings() {
  const [s, o, d] = await Promise.all([api('/settings'), api('/settings/options'), api('/settings/defaults')]);
  S = s; DEFAULTS = d;
  LANE_LABEL = o.labels || {};
  FIELDS.lane.o = Object.fromEntries(o.lanes.map(l => [l, l === 'auto' ? 'Automatic (best resume per job)' : laneLabel(l)]));
  presets = await api('/presets');
}
function fieldHtml(f) {
  const v = getk(f.k);
  if (f.t === 'bool') return `<label class="check f" ${f.mode && f.mode !== S.schedule ? 'style="opacity:.5"' : ''}><input type="checkbox" data-k="${f.k}" ${v ? 'checked' : ''}><span>${esc(f.l)}</span></label>`;
  let h = '';
  if (f.t === 'multi') h = `<div class="opts">${Object.entries(f.o).map(([k, l]) => `<label><input type="checkbox" data-k="${f.k}" value="${esc(k)}" ${v.includes(k) ? 'checked' : ''}>${esc(l)}</label>`).join('')}</div>`;
  if (f.t === 'list') h = `<input type="text" data-k="${f.k}" value="${esc(v.join(', '))}">`;
  if (f.t === 'num') h = `<input type="number" data-k="${f.k}" value="${v}" min="0">`;
  if (f.t === 'time') h = `<input type="time" data-k="${f.k}" value="${esc(v)}" style="max-width:160px">`;
  if (f.t === 'select') h = `<select data-k="${f.k}">${Object.entries(f.o).map(([k, l]) => `<option value="${esc(k)}" ${String(v) === k ? 'selected' : ''}>${esc(l)}</option>`).join('')}</select>`;
  if (f.t === 'range') h = `<div class="range-row"><input type="range" data-k="${f.k}" min="0" max="1" step="0.05" value="${v}"><output>${v}</output></div>`;
  const off = f.mode && f.mode !== S.schedule;  // e.g. minute intervals while the daily schedule is on
  return `<div class="f" ${off ? 'style="opacity:.5"' : ''}><div class="l">${esc(f.l)}</div>${h}${f.h ? `<div class="hint">${esc(f.h)}</div>` : ''}</div>`;
}
function bindFields(root, onChange) {
  $$('[data-k]', root).forEach(el => {
    const f = FIELDS[el.dataset.k];
    el.addEventListener('change', () => {
      if (f.t === 'multi') setk(f.k, $$(`[data-k="${f.k}"]:checked`, root).map(x => x.value));
      else if (f.t === 'bool') setk(f.k, el.checked);
      else if (f.t === 'list') setk(f.k, el.value.split(',').map(x => x.trim()).filter(Boolean));
      else if (f.t === 'num' || f.t === 'range' || f.num) setk(f.k, Number(el.value) || 0);
      else setk(f.k, el.value);
      onChange();
    });
    if (f.t === 'range') el.oninput = () => el.nextElementSibling.textContent = el.value;
  });
}
const openGroups = new Set(store.get('openGroups', FILTERS.filter(g => g[1]).map(g => g[0])));
function renderFilters() {
  $('#filters').innerHTML = `
    <div class="card" style="margin-bottom:8px"><div class="bd">
      <div class="f" style="margin-top:0"><div class="l">Saved views</div>
        <select id="preset"><option value="">— current filters —</option>${Object.keys(presets).map(n => `<option>${esc(n)}</option>`).join('')}</select></div>
      <div class="row"><button class="btn sm" id="presetSave">Save view…</button><button class="btn sm" id="presetDel">Delete</button>
        <button class="btn sm ghost" id="resetFilters">Reset all</button></div></div></div>
    ${FILTERS.map(([g, , fs]) => `<details data-g="${esc(g)}" ${openGroups.has(g) ? 'open' : ''}><summary>${esc(g)}</summary><div class="grp">${fs.map(fieldHtml).join('')}</div></details>`).join('')}`;
  $$('#filters details').forEach(d => d.ontoggle = () => { d.open ? openGroups.add(d.dataset.g) : openGroups.delete(d.dataset.g); store.set('openGroups', [...openGroups]); });
  bindFields($('#filters'), saveFilters);
  $('#preset').onchange = async e => { const n = e.target.value; if (!n) return; S = await put('/settings', presets[n]); renderFilters(); $('#preset').value = n; loadJobs(); };
  $('#presetSave').onclick = async () => { const n = prompt('Name this view:'); if (!n) return; presets = await put(`/presets/${enc(n)}`, S); renderFilters(); $('#preset').value = n; toast('View saved'); };
  $('#presetDel').onclick = async () => { const n = $('#preset').value; if (!n) return toast('Pick a saved view first'); if (!confirm(`Delete view “${n}”?`)) return; presets = await del(`/presets/${enc(n)}`); renderFilters(); };
  $('#resetFilters').onclick = async () => { if (!confirm('Reset every filter and setting to defaults?')) return; S = await del('/settings'); renderFilters(); loadJobs(); };
}
let saveTimer;
function saveFilters() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(async () => {
    try { S = await put('/settings', S); if ($('#preset')) $('#preset').value = ''; renderActiveFilters(); loadJobs(); } catch (e) { toast(`Invalid setting: ${e.message}`); }
  }, 300);
}
function renderActiveFilters() {
  const chips = [];
  for (const [, , fs] of FILTERS) for (const f of fs) {
    if (f.t === 'range' || ['freshness_half_life_days', 'new_badge_hours', 'allow_boost', 'stale_days', 'h1b_min_approvals'].includes(f.k)) continue;
    const v = getk(f.k), d = f.k.split('.').reduce((o, p) => o?.[p], DEFAULTS);
    if (JSON.stringify(v) === JSON.stringify(d)) continue;
    let txt = Array.isArray(v) ? (v.length ? v.map(x => f.o?.[x] || x).join(', ') : 'any') : f.t === 'bool' ? (v ? 'on' : 'off') : (f.o?.[v] || v);
    chips.push(`<span class="tag ac" title="Click to reset">${esc(f.l)}: ${esc(txt)} <a href="#" data-reset="${f.k}">✕</a></span>`);
  }
  $('#activeFilters').innerHTML = chips.join('');
  $$('[data-reset]').forEach(a => a.onclick = e => { e.preventDefault(); setk(a.dataset.reset, structuredClone(a.dataset.reset.split('.').reduce((o, p) => o?.[p], DEFAULTS))); renderFilters(); saveFilters(); });
}
async function loadJobs() {
  $('#list').innerHTML = '<div class="empty"><span class="spin"></span> Ranking…</div>';
  feed = await api(`/jobs?show_hidden=${showHidden}`);
  renderActiveFilters();
  renderList();
}
function sorted(rows) {
  const by = { score: (a, b) => b.score - a.score, new: (a, b) => new Date(b.posted_at) - new Date(a.posted_at),
    relevance: (a, b) => b.relevance - a.relevance, deadline: (a, b) => (a.days_left ?? 1e9) - (b.days_left ?? 1e9) }[sort];
  return [...rows].sort(by);
}
function visibleRows() {
  const m = j => !q || `${j.title} ${j.company} ${j.location}`.toLowerCase().includes(q);
  return [...sorted(feed.shown.filter(m)), ...(showHidden ? feed.hidden.filter(m) : [])];
}
function renderList() {
  const rows = visibleRows(), unseen = feed.shown.filter(isUnseen).length;
  $('#counts').innerHTML = `<b>${feed.shown_count.toLocaleString()}</b> shown · ${feed.hidden_count.toLocaleString()} hidden${unseen ? ` · <b class="ok">${unseen} new since last visit</b> <a href="#" id="markSeen">mark seen</a>` : ''}`;
  if ($('#markSeen')) $('#markSeen').onclick = e => { e.preventDefault(); lastVisit = Date.now(); store.set('lastVisit', lastVisit); renderList(); };
  $('#list').innerHTML = rows.length ? rows.map(listItem).join('') : `<div class="empty">No jobs match these filters.<br><span class="small">Try a wider “Posted within”, or clear a category.</span></div>`;
  $$('#list .job').forEach(el => el.onclick = () => select(el.dataset.id));
  if (!rows.find(r => r.id === selected)) selected = rows[0]?.id || null;
  if (selected) select(selected, false); else $('#detail').innerHTML = '<div class="card"><div class="empty">Select a job to see the details.</div></div>';
}
function listItem(j) {
  const hid = j.hidden_reasons.length;
  return `<div class="job ${j.id === selected ? 'sel' : ''} ${hid ? 'is-hidden' : ''}" data-id="${esc(j.id)}">
    <div class="t">${isUnseen(j) ? '<span class="unseen-dot" title="New since your last visit"></span>' : ''}${esc(j.title)}</div>
    <div class="score ${scoreCls(j.score)}" title="Score out of 100">${Math.round(j.score)}</div>
    <div class="meta">${esc(j.company)} · ${esc(shortLoc(j.location))} · ${ago(j.posted_at)}</div>
    <div class="tags">${j.is_new ? '<span class="tag ok">NEW</span>' : ''}${cptTag(j)}
      ${j.status ? `<span class="tag ac">${SLABEL[j.status]}</span>` : ''}
      ${j.days_left != null && j.days_left >= 0 && j.days_left <= 14 ? `<span class="tag warn">${Math.ceil(j.days_left)}d left</span>` : ''}
      ${j.hourly ? `<span class="tag">$${j.hourly[1]}/hr</span>` : ''}
      ${hid ? `<span class="tag bad">hidden: ${esc(j.hidden_reasons[0])}${hid > 1 ? ` +${hid - 1}` : ''}</span>` : ''}</div></div>`;
}
async function select(id, scroll = true) {
  selected = id;
  $$('#list .job').forEach(el => el.classList.toggle('sel', el.dataset.id === id));
  if (scroll) $(`#list .job[data-id="${CSS.escape(id)}"]`)?.scrollIntoView({ block: 'nearest' });
  const row = [...feed.shown, ...feed.hidden].find(r => r.id === id);
  if (row) renderDetail(row, null);
  const d = detailCache[id] || (detailCache[id] = await api(`/jobs/${enc(id)}`));
  if (selected === id) renderDetail({ ...row, ...d }, d);
}
function renderDetail(j, full) {
  const parts = j.score_parts || {}, total = Object.values(parts).reduce((a, b) => a + b, 0) || 1;
  const rp = j.relevance_parts || {};
  $('#detail').innerHTML = `
    <div class="card"><div class="bd">
      <div class="row between"><span class="small mut">${esc(j.company)}</span><span class="score ${scoreCls(j.score)}" style="grid-row:auto">${Math.round(j.score ?? 0)}</span></div>
      <div class="title">${esc(j.title)}</div>
      <div class="small mut">${esc(j.location || 'Location not listed')}</div>
      <div class="small mut">Posted ${ago(j.posted_at)} (${day(j.posted_at)}) · via ${esc(SRC[j.source] || j.source)}${j.also?.length ? ` · also on ${j.also.map(a => esc(SRC[a.source] || a.source)).join(', ')}` : ''}</div>
      <div class="actions">
        <button class="btn primary" data-act="apply" title="Opens the posting; Simplify can fill it. Shortcut: a">Apply ↗</button>
        <button class="btn" data-act="autofill" title="A separate browser fills the form and stops before Submit">Autofill</button>
        <button class="btn" data-act="saved">☆ Save</button>
        <button class="btn" data-act="applied">✓ Mark applied</button>
        <button class="btn danger" data-act="dismissed" title="Hide it and teach the ranking. Shortcut: x">✕ Not interested</button>
      </div>
      ${j.status ? `<p class="small" style="margin:10px 0 0">In your tracker as <b>${SLABEL[j.status]}</b>.</p>` : ''}
      ${j.hidden_reasons?.length ? `<p class="small bad" style="margin:10px 0 0">Hidden by your filters: ${j.hidden_reasons.map(esc).join(' · ')}</p>` : ''}
    </div></div>

    <div class="card"><div class="hd"><h3>Why it ranks here</h3><span class="small mut">${Math.round(j.score ?? 0)} / 100</span></div><div class="bd">
      <div class="bars">${Object.entries(parts).map(([k, v]) => `<i style="width:${100 * v / total}%;background:var(${PARTS[k]?.[1] || '--faint'})" title="${PARTS[k]?.[0] || k}: ${v}"></i>`).join('')}</div>
      <div class="legend">${Object.entries(parts).map(([k, v]) => `<span><span class="sw" style="background:var(${PARTS[k]?.[1] || '--faint'})"></span>${PARTS[k]?.[0] || k}: <b>${v}</b></span>`).join('')}</div>
      <p class="small mut" style="margin:10px 0 0">Resume match ${Math.round((j.relevance ?? 0) * 100)}% = meaning ${Math.round((rp.semantic ?? 0) * 100)}% · skills ${Math.round((rp.skills ?? 0) * 100)}% · title ${Math.round((rp.title ?? 0) * 100)}%
        (best resume: <b>${esc(laneLabel(j.best_lane))}</b>)${j.personal != null ? ` · your-taste model: ${Math.round(j.personal * 100)}%` : ''}</p>
    </div></div>

    <div class="card"><div class="hd"><h3>Eligibility for you (F-1 CPT)</h3></div><div class="bd"><div class="kv">
      <span>CPT</span><span>${cptTag(j)} <span class="small mut">${esc(j.cpt_label || '')}${j.cpt_hits?.length ? ` — “${esc(j.cpt_hits.slice(0, 2).join('”, “'))}”` : ''}</span></span>
      <span>H-1B history</span><span>${j.h1b_approvals ? `${j.h1b_approvals.toLocaleString()} approvals (USCIS FY2025–26)` : '<span class="mut">none found</span>'}</span>
      <span>Restrictions</span><span>${j.auth?.length ? j.auth.map(a => `<span class="tag bad">${esc(a.replace('_', ' '))}</span>`).join(' ') : '<span class="ok">none detected</span>'}</span>
      <span>Education</span><span>${j.education?.length ? j.education.map(e => `<span class="tag warn">${esc(e.replace(/_/g, ' '))}</span>`).join(' ') : '<span class="ok">open to grad students</span>'}</span>
      <span>Season</span><span>${j.seasons?.length ? j.seasons.map(esc).join(', ') : '<span class="mut">not stated</span>'}</span>
      <span>Work mode</span><span>${esc(j.work_mode || '—')}</span>
      <span>Pay</span><span>${j.hourly ? `$${j.hourly[0]}${j.hourly[1] !== j.hourly[0] ? `–${j.hourly[1]}` : ''}/hour` : '<span class="mut">not listed</span>'}</span>
      <span>Deadline</span><span>${j.deadline ? `${dateOnly(j.deadline)} (${j.days_left < 0 ? 'passed' : `${Math.ceil(j.days_left)} days left`})` : '<span class="mut">not stated (usually rolling)</span>'}</span>
      ${j.warnings?.length ? `<span>Heads-up</span><span>${j.warnings.map(w => `<span class="tag warn">${esc(w)}</span>`).join(' ')}</span>` : ''}
    </div></div></div>

    <div class="card"><div class="hd"><h3>Resume help</h3><button class="btn sm" data-act="prompt" title="Copies a prompt you can paste into Claude — nothing is sent automatically">Copy Claude prompt</button></div>
      <div class="bd" id="resumeHelp"><span class="spin"></span> <span class="small mut">Comparing with your resumes…</span></div></div>

    <div class="card"><div class="hd"><h3>Application packet</h3><span class="small mut">click any value to copy</span></div><div class="bd" id="packet"><span class="spin"></span></div></div>

    <div class="card"><div class="hd"><h3>Description</h3>${full?.description ? '<button class="btn sm ghost" id="moreDesc">Show all</button>' : ''}</div><div class="bd">
      ${full ? (full.description ? `<div class="desc" id="desc">${esc(full.description)}</div>` : '<p class="mut small" style="margin:0">Only the title is known so far — the description is fetched automatically when the source allows it. Open the posting to read it.</p>') : '<span class="spin"></span>'}
      ${j.also?.length ? `<p class="small" style="margin:10px 0 0">Other listings: ${j.also.map(a => `<a href="${esc(a.url)}" target="_blank" rel="noopener">${esc(SRC[a.source] || a.source)}${a.location ? ` (${esc(shortLoc(a.location))})` : ''}</a>`).join(' · ')}</p>` : ''}
    </div></div>`;
  $$('#detail [data-act]').forEach(b => b.onclick = () => act(b.dataset.act, j, b));
  if ($('#moreDesc')) $('#moreDesc').onclick = () => { $('#desc').classList.toggle('open'); $('#moreDesc').textContent = $('#desc').classList.contains('open') ? 'Show less' : 'Show all'; };
  if (full) { loadResumeHelp(j.id); loadPacket(j.id); }
}
const helpCache = {};
async function loadResumeHelp(id) {
  const h = helpCache[id] || (helpCache[id] = await api(`/jobs/${enc(id)}/resume-help`));
  if (selected !== id || !$('#resumeHelp')) return;
  const lanes = Object.entries(h.lane_similarity);
  $('#resumeHelp').innerHTML = `
    <p class="small" style="margin:0 0 8px">Send your <b>${esc(laneLabel(h.lane))}</b> resume
      <span class="mut">(${lanes.map(([k, v]) => `${esc(laneLabel(k))} ${Math.round(v * 100)}%`).join(' vs ')})</span> · <a href="/resume/${enc(h.lane)}" target="_blank">open file</a> · <a href="#resumes">update it</a></p>
    <h4>Lead with these bullets</h4>
    <ol class="bullets small">${h.bullets.map(b => `<li>${esc(b.text)} <span class="faint">(${Math.round(b.similarity * 100)}%)</span></li>`).join('')}</ol>
    <h4 style="margin-top:10px">Skills</h4>
    <div class="tags" style="margin-top:4px">${h.matched.map(s => `<span class="tag ok">✓ ${esc(s)}</span>`).join('')}
      ${h.missing_but_on_other_resume.map(s => `<span class="tag ac" title="It's on your other resume — add it to this one">+ ${esc(s)} (on other resume)</span>`).join('')}
      ${h.missing.map(s => `<span class="tag bad" title="The posting asks for it; it's on neither resume">✗ ${esc(s)}</span>`).join('')}
      ${!h.matched.length && !h.missing.length && !h.missing_but_on_other_resume.length ? '<span class="small mut">No dictionary skills found in this posting.</span>' : ''}</div>`;
  $('#detail [data-act="prompt"]').onclick = () => copyText(h.prompt, 'Prompt copied — paste it into Claude');
}
async function loadPacket(id) {
  const p = await api(`/jobs/${enc(id)}/packet`);
  if (selected !== id || !$('#packet')) return;
  const cv = v => `<span class="copy" data-copy="${esc(v)}">${esc(v)}</span>`;
  $('#packet').innerHTML = `${p.worked_here ? '<p class="small ok" style="margin:0 0 8px">You have worked at this company — answer “Yes” to former-employee questions.</p>' : ''}
    <table class="t"><tbody>${p.fields.map(f => `<tr><td class="k">${esc(f.label)}</td><td>${cv(f.value)}</td></tr>`).join('')}</tbody></table>
    <h4 style="margin:12px 0 4px">Common questions</h4>
    <table class="t"><tbody>${p.answers.map(a => `<tr><td class="k">${esc(a.question)}</td><td>${cv(a.answer)}${a.note ? `<div class="tiny faint">${esc(a.note)}</div>` : ''}</td></tr>`).join('')}</tbody></table>
    <p class="tiny faint" style="margin:8px 0 0">Edit these on the Profile page.</p>`;
}
async function setStatus(id, status, extra = {}) { await put(`/applications/${enc(id)}`, { status, ...extra }); }
async function act(what, j, btn) {
  if (what === 'apply') { window.open(j.url, '_blank', 'noopener'); await setStatus(j.id, 'opened', { source: 'apply_prompt' }); pending = j; return; }
  if (what === 'autofill') {
    btn.disabled = true; btn.textContent = 'Opening browser…';
    try { const r = await post(`/autofill/${enc(j.id)}`); if (r.error) { toast(r.error); btn.textContent = 'Unavailable'; } else { btn.textContent = 'Browser opened'; pending = j; } }
    catch (e) { toast(e.message); btn.textContent = 'Failed'; }
    return;
  }
  if (['saved', 'applied', 'dismissed'].includes(what)) {
    await setStatus(j.id, what);
    toast({ saved: 'Saved to tracker', applied: 'Marked applied', dismissed: 'Hidden — the ranking learns from this' }[what]);
    detailCache = {}; const idx = visibleRows().findIndex(r => r.id === j.id); await loadJobs();
    const rows = visibleRows(); if (what === 'dismissed' && rows.length) select(rows[Math.min(idx, rows.length - 1)].id);
  }
}
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState !== 'visible' || !pending) return;
  $('#askBody').innerHTML = `<b>${esc(pending.title)}</b> at ${esc(pending.company)}`; $('#askDialog').showModal();
});
$$('#askDialog [data-answer]').forEach(b => b.onclick = async () => {
  const j = pending; pending = null; $('#askDialog').close();
  await setStatus(j.id, b.dataset.answer, { source: 'apply_prompt' }); toast(SLABEL[b.dataset.answer]);
  if (!$('#page-jobs').classList.contains('hidden')) loadJobs();
});
$('#q').oninput = e => { q = e.target.value.toLowerCase(); renderList(); };
$('#sort').value = sort; $('#sort').onchange = e => { sort = e.target.value; store.set('sort', sort); renderList(); };
$('#showHidden').onchange = e => { showHidden = e.target.checked; loadJobs(); };
document.addEventListener('keydown', e => {
  if ($('#page-jobs').classList.contains('hidden') || e.target.matches('input, textarea, select') || e.metaKey || e.ctrlKey) return;
  const rows = visibleRows(), i = rows.findIndex(r => r.id === selected), j = rows[i];
  if (e.key === 'j' && i < rows.length - 1) select(rows[i + 1].id);
  else if (e.key === 'k' && i > 0) select(rows[i - 1].id);
  else if (j && e.key === 'a') act('apply', j);
  else if (j && e.key === 's') act('saved', j);
  else if (j && e.key === 'x') act('dismissed', j);
});

// ---------- TRACKER ----------
let trackView = store.get('trackView', 'board'), trackQ = '';
async function renderTracker() {
  const [rows, st] = await Promise.all([api('/applications'), api('/applications/stats')]);
  const by = st.by_status, today = new Date().toISOString().slice(0, 10);
  const f = rows.filter(a => !trackQ || `${a.company} ${a.title}`.toLowerCase().includes(trackQ));
  const cols = ['saved', 'opened', 'applied', 'oa', 'interview', 'offer', 'rejected', 'ghosted'];
  $('#page-tracker').innerHTML = `
    <div class="page-head"><div><h1>Tracker</h1><p>Drag cards between columns, or change status in the table. Gmail updates move them automatically.</p></div>
      <div class="row"><div class="seg"><button data-view="board" class="${trackView === 'board' ? 'on' : ''}">Board</button><button data-view="table" class="${trackView === 'table' ? 'on' : ''}">Table</button></div>
        <button class="btn" id="addManual">+ Add an application</button><a class="btn" href="/applications.csv">Export CSV</a></div></div>
    <div class="kpis">${kpi(st.applied_total, 'Applied in total')}${kpi(st.applied_last_7d, 'Applied this week')}${kpi(`${Math.round(st.response_rate * 100)}%`, 'Got a response')}
      ${kpi(by.oa + by.interview, 'Assessments & interviews')}${kpi(by.offer, 'Offers')}${kpi(st.follow_ups_due, 'Follow-ups due')}</div>
    <div class="row" style="margin-bottom:10px"><input type="text" id="trackQ" placeholder="Search company or role…" value="${esc(trackQ)}" style="max-width:320px"></div>
    ${!rows.length ? '<div class="card"><div class="empty">Nothing tracked yet. Save or apply to a job and it shows up here.</div></div>'
      : trackView === 'board' ? `<div class="board">${cols.map(c => `<div class="col" data-col="${c}"><h3>${SLABEL[c]} <span class="mut">${f.filter(a => a.status === c).length}</span></h3>
          ${f.filter(a => a.status === c).map(a => tcard(a, today)).join('')}</div>`).join('')}</div>
          <p class="small mut">Withdrawn and “not interested” jobs are only in the table view.</p>`
      : `<div class="card"><table class="t"><thead><tr><th>Job</th><th>Status</th><th>Applied</th><th>Follow up</th><th title="Turned down because of CPT / visa">CPT issue</th><th>Notes</th><th></th></tr></thead><tbody>
          ${f.map(a => `<tr><td><b>${esc(a.title)}</b><div class="small mut">${esc(a.company)}${a.url ? ` · <a href="${esc(a.url)}" target="_blank" rel="noopener">posting</a>` : ''}</div>
            <div class="tiny faint">updated ${ago(a.updated_at)} · by ${esc(a.source)}</div></td>
            <td><select data-f="status" data-id="${esc(a.job_id)}">${STATUSES.map(s => `<option value="${s}" ${s === a.status ? 'selected' : ''}>${SLABEL[s]}</option>`).join('')}</select></td>
            <td class="small">${day(a.applied_at)}</td>
            <td><input type="date" data-f="follow_up" data-id="${esc(a.job_id)}" value="${esc(a.follow_up || '')}" ${a.follow_up && a.follow_up <= today ? 'style="border-color:var(--warn)"' : ''}></td>
            <td><input type="checkbox" data-f="cpt_issue" data-id="${esc(a.job_id)}" ${a.cpt_issue ? 'checked' : ''}></td>
            <td><input type="text" data-f="notes" data-id="${esc(a.job_id)}" value="${esc(a.notes)}" placeholder="Add a note"></td>
            <td><button class="btn sm ghost" data-del="${esc(a.job_id)}" title="Remove from tracker">✕</button></td></tr>`).join('')}</tbody></table></div>`}`;
  $$('[data-view]').forEach(b => b.onclick = () => { trackView = b.dataset.view; store.set('trackView', trackView); renderTracker(); });
  $('#trackQ').oninput = e => { trackQ = e.target.value.toLowerCase(); renderTracker().then(() => { const i = $('#trackQ'); i.focus(); i.setSelectionRange(trackQ.length, trackQ.length); }); };
  $('#addManual').onclick = async () => { const company = prompt('Company:'); if (!company) return; const title = prompt('Role title:') || ''; await post('/applications/manual', { company, title }); renderTracker(); };
  $$('#page-tracker [data-f]').forEach(el => el.onchange = async () => {
    const a = rows.find(x => x.job_id === el.dataset.id), k = el.dataset.f;
    const v = el.type === 'checkbox' ? el.checked : el.value;
    await setStatus(el.dataset.id, k === 'status' ? v : a.status, k === 'status' ? {} : { [k]: v });
    if (k === 'cpt_issue') toast(v ? 'Noted — this company will rank lower' : 'Cleared'); else if (k === 'status') renderTracker();
  });
  $$('#page-tracker [data-del]').forEach(b => b.onclick = async () => { if (confirm('Remove this application from the tracker?')) { await del(`/applications/${enc(b.dataset.del)}`); renderTracker(); } });
  // drag & drop between columns
  $$('.tcard').forEach(c => c.ondragstart = e => e.dataTransfer.setData('text/plain', c.dataset.id));
  $$('.col').forEach(col => {
    col.ondragover = e => { e.preventDefault(); col.classList.add('drop'); };
    col.ondragleave = () => col.classList.remove('drop');
    col.ondrop = async e => { e.preventDefault(); col.classList.remove('drop'); const id = e.dataTransfer.getData('text/plain'); await setStatus(id, col.dataset.col); renderTracker(); };
  });
  $$('.tcard [data-cpt]').forEach(b => b.onclick = async e => { e.stopPropagation(); const a = rows.find(x => x.job_id === b.dataset.cpt); await setStatus(a.job_id, a.status, { cpt_issue: !a.cpt_issue }); renderTracker(); });
}
function tcard(a, today) {
  const due = a.follow_up && a.follow_up <= today && ['applied', 'oa', 'interview'].includes(a.status);
  return `<div class="tcard ${due ? 'due' : ''}" draggable="true" data-id="${esc(a.job_id)}">
    <div class="t">${esc(a.company)}</div><div class="small">${esc(a.title)}</div>
    <div class="tiny mut" style="margin-top:4px">${a.applied_at ? `applied ${day(a.applied_at)}` : `added ${day(a.updated_at)}`}${a.follow_up ? ` · follow up ${esc(a.follow_up)}` : ''}</div>
    ${a.notes ? `<div class="tiny" style="margin-top:4px">${esc(a.notes)}</div>` : ''}
    <div class="row" style="margin-top:6px">${a.url ? `<a class="tiny" href="${esc(a.url)}" target="_blank" rel="noopener">posting</a>` : ''}
      ${a.status === 'rejected' ? `<a href="#" class="tiny ${a.cpt_issue ? 'bad' : 'mut'}" data-cpt="${esc(a.job_id)}">${a.cpt_issue ? '✓ rejected over CPT' : 'rejected over CPT?'}</a>` : ''}</div></div>`;
}

// ---------- INBOX ----------
let inboxTab = 'new';
async function renderInbox() {
  const [g, items, handled] = await Promise.all([api('/gmail'), api('/inbox?state=new'), api('/inbox?state=applied_to_tracker')]);
  const ls = g.last_scan, list = inboxTab === 'new' ? items : handled;
  $('#page-inbox').innerHTML = `
    <div class="page-head"><div><h1>Inbox</h1><p>Job emails from Gmail. Emails that clearly match a tracked application update it automatically; the rest wait here.</p></div></div>
    <div class="card" style="margin-bottom:16px">${g.connected ? `<div class="bd row between">
        <span>📬 Connected as <b>${esc(g.email)}</b> · <span class="mut">${ls ? `last scan ${ago(ls.last_run)}${ls.error ? ` — <span class="bad">${esc(ls.error)}</span>` : ` — ${ls.job_emails ?? 0} job emails, ${ls.updated ?? 0} updated`}` : 'not scanned yet'}</span></span>
        <span class="row"><button class="btn primary" id="gScan">Scan now</button><button class="btn" id="gDis">Disconnect</button></span></div>`
      : `<div class="hd"><h2>Connect Gmail (read-only, free)</h2></div><div class="bd">
        <ol class="steps small"><li>Turn on 2-Step Verification for your Google account.</li>
          <li>Create an <a href="https://myaccount.google.com/apppasswords" target="_blank" rel="noopener">app password</a> named “JobPrep”.</li>
          <li>Paste it below. It’s stored in your Mac’s Keychain — never in a file — and JobPrep only reads mail.</li></ol>
        <div class="form-grid" style="margin-top:10px"><label class="field"><span>Gmail address</span><input type="email" id="gEmail" placeholder="you@gmail.com"></label>
          <label class="field"><span>App password</span><input type="password" id="gPw" placeholder="16 characters" autocomplete="off"></label>
          <div style="align-self:end"><button class="btn primary" id="gCon">Connect</button> <span id="gMsg" class="small mut"></span></div></div></div>`}</div>
    <div class="row between" style="margin-bottom:10px"><div class="seg"><button data-itab="new" class="${inboxTab === 'new' ? 'on' : ''}">To review (${items.length})</button>
      <button data-itab="handled" class="${inboxTab === 'handled' ? 'on' : ''}">In tracker (${handled.length})</button></div></div>
    ${list.length ? list.map(m => inboxCard(m)).join('') : `<div class="card"><div class="empty">${inboxTab === 'new' ? 'Nothing to review.' : 'No emails linked to the tracker yet.'}</div></div>`}`;
  $$('[data-itab]').forEach(b => b.onclick = () => { inboxTab = b.dataset.itab; renderInbox(); });
  if ($('#gCon')) $('#gCon').onclick = async () => { $('#gMsg').textContent = 'Checking…'; try { await post('/gmail/connect', { email: $('#gEmail').value, app_password: $('#gPw').value }); toast('Gmail connected'); renderInbox(); } catch (e) { $('#gMsg').textContent = e.message; } };
  if ($('#gScan')) $('#gScan').onclick = async () => { $('#gScan').innerHTML = '<span class="spin"></span> Scanning'; try { const r = await post('/gmail/scan'); toast(r.error || `${r.job_emails} job emails · ${r.updated} updated · ${r.to_review} to review`); } catch (e) { toast(e.message); } renderInbox(); loadInboxCount(); };
  if ($('#gDis')) $('#gDis').onclick = async () => { if (confirm('Disconnect Gmail and delete the saved app password?')) { await del('/gmail'); renderInbox(); } };
  const done = async (msg) => { toast(msg); await renderInbox(); loadInboxCount(); };
  const resolve = (id, body) => post(`/inbox/${enc(id)}/resolve`, body);
  $$('[data-attach]').forEach(b => b.onclick = async () => { await resolve(b.dataset.msg, { job_id: b.dataset.attach }); done('Added to tracker'); });
  $$('[data-dis]').forEach(b => b.onclick = async () => { await post(`/inbox/${enc(b.dataset.dis)}/dismiss`); done('Dismissed'); });
  $$('form[data-new]').forEach(f => f.onsubmit = async e => { e.preventDefault();
    await resolve(f.dataset.new, { company: f.company.value.trim(), title: f.title.value.trim() }); done('New tracker entry created'); });
  $$('[data-search]').forEach(inp => {
    const box = inp.nextElementSibling; let timer;
    inp.oninput = () => { clearTimeout(timer); timer = setTimeout(async () => {
      if (inp.value.trim().length < 2) { box.innerHTML = ''; return; }
      const hits = await api(`/jobs-search?q=${enc(inp.value)}`);
      box.innerHTML = hits.length ? hits.map(h => `<button class="btn sm" data-attach="${esc(h.job_id)}" data-msg="${esc(inp.dataset.search)}">${esc(h.company)} — ${esc(h.title)}${h.tracked ? ` <span class="tag ac">${SLABEL[h.tracked]}</span>` : ''}${h.closed ? ' <span class="faint">(closed)</span>' : ''}</button>`).join('')
        : '<span class="small mut">No jobs found — create a new entry below.</span>';
      $$('[data-attach]', box).forEach(b => b.onclick = async () => { await resolve(b.dataset.msg, { job_id: b.dataset.attach }); done('Added to tracker'); });
    }, 250); };
  });
}
function inboxCard(m) {
  const kindTag = `<span class="tag ${m.kind === 'rejected' ? 'bad' : ['offer', 'interview'].includes(m.kind) ? 'ok' : 'ac'}">${SLABEL[m.kind] || m.kind}</span>`;
  const head = `<div class="row between"><b>${esc(m.subject)}</b>${kindTag}</div>
    <div class="small mut">${esc(m.sender)} · ${ago(m.received_at)}</div>`;
  if (m.state !== 'new') return `<div class="card" style="margin-bottom:10px"><div class="bd">${head}
    <p class="small" style="margin:8px 0 0">${m.linked ? `In tracker as <b>${esc(m.linked.company)} — ${esc(m.linked.title)}</b>` : 'Linked to the tracker'}</p></div></div>`;
  const id = esc(m.message_id);
  return `<div class="card" style="margin-bottom:10px"><div class="bd">${head}
    <p class="small" style="margin:8px 0">${esc(m.snippet.slice(0, 260))}…</p>
    <p class="small" style="margin:0 0 8px">Looks like: <b>${esc(m.role_guess || 'role not stated')}</b> at <b>${esc(m.company_guess || 'unknown company')}</b></p>
    ${m.suggestions.length ? `<h4>Best matches</h4><div class="row" style="margin:4px 0 10px">${m.suggestions.map(x =>
        `<button class="btn sm ${x === m.suggestions[0] ? 'primary' : ''}" data-attach="${esc(x.job_id)}" data-msg="${id}" title="Attach this email to it">${esc(x.company)} — ${esc(x.title)}${x.tracked ? ` · ${SLABEL[x.tracked]}` : ''}${x.closed ? ' · closed' : ''}</button>`).join('')}</div>` : ''}
    <h4>Find another job</h4>
    <input type="text" data-search="${id}" placeholder="Type company or role, e.g. “gilead intern”" style="margin:4px 0 6px;max-width:420px">
    <div class="row" style="margin-bottom:10px"></div>
    <h4>Or add it as a new application</h4>
    <form class="row" data-new="${id}" style="margin-top:4px">
      <input type="text" name="company" value="${esc(m.company_guess)}" placeholder="Company" style="max-width:220px" required>
      <input type="text" name="title" value="${esc(m.role_guess)}" placeholder="Role (optional)" style="max-width:300px">
      <button class="btn">+ Add to tracker as ${esc(SLABEL[m.kind] || m.kind)}</button>
      <button type="button" class="btn ghost" data-dis="${id}">Not a job email</button></form></div></div>`;
}

// ---------- RESUMES ----------
let lastImpact = null;
async function upload(url, method, fields) {
  const fd = new FormData();
  for (const [k, v] of Object.entries(fields)) fd.append(k, v);
  const r = await fetch(url, { method, body: fd });
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.detail || r.statusText);
  return body;
}
async function resumeChange(promise, msg) {
  const btns = $$('#page-resumes button'); btns.forEach(b => b.disabled = true);
  toast('Re-ranking every job with your resume…');
  try { const r = await promise; lastImpact = r.impact; detailCache = {}; Object.keys(helpCache).forEach(k => delete helpCache[k]); await loadSettings(); toast(msg); }
  catch (e) { toast(e.message); }
  renderResumes();
}
async function renderResumes() {
  const { resumes } = await api('/resumes');
  const im = lastImpact;
  const delta = (a, b, pct) => { const d = pct ? Math.round((b - a) * 100) : Math.round((b - a) * 10) / 10; return `<b class="${d > 0 ? 'ok' : d < 0 ? 'bad' : ''}">${d > 0 ? '+' : ''}${d}${pct ? ' pts' : ''}</b>`; };
  $('#page-resumes').innerHTML = `
    <div class="page-head"><div><h1>Resumes</h1><p>Every job is scored against these. Upload a new version any time — the ranking updates right away.</p></div>
      <button class="btn primary" id="addResumeBtn">+ Add a resume</button></div>
    ${im ? `<div class="card" style="margin-bottom:16px;border-color:var(--ac)"><div class="hd"><h2>What changed in your top ${im.top_n}</h2><button class="btn sm ghost" id="hideImpact">✕</button></div><div class="bd small">
      <p style="margin:0 0 8px">Average score ${im.avg_score_before} → <b>${im.avg_score_after}</b> (${delta(im.avg_score_before, im.avg_score_after)}) ·
        average resume match ${Math.round(im.avg_match_before * 100)}% → <b>${Math.round(im.avg_match_after * 100)}%</b> (${delta(im.avg_match_before, im.avg_match_after, true)})</p>
      <div class="settings-grid"><div><h4>New in your top ${im.top_n} (${im.entered.length})</h4>${im.entered.length ? im.entered.map(r => `<div class="list-row" data-open="${esc(r.id)}" style="padding:5px 0"><span class="score ${scoreCls(r.score)}">${Math.round(r.score)}</span><span class="grow">${esc(r.title)} <span class="mut">· ${esc(r.company)}</span></span></div>`).join('') : '<p class="mut">none</p>'}</div>
        <div><h4>Dropped out (${im.left.length})</h4>${im.left.length ? im.left.map(r => `<div class="list-row" data-open="${esc(r.id)}" style="padding:5px 0"><span class="score">${Math.round(r.score)}</span><span class="grow">${esc(r.title)} <span class="mut">· ${esc(r.company)}</span></span></div>`).join('') : '<p class="mut">none</p>'}</div></div></div></div>` : ''}
    <div class="card hidden" id="addResume" style="margin-bottom:16px"><div class="hd"><h2>Add a resume</h2></div><div class="bd">
      <p class="small mut" style="margin:0 0 10px">Add one per kind of role you apply to (e.g. “Data analyst”). Each job is matched to whichever fits best.</p>
      <form class="form-grid" id="addResumeForm">
        <label class="field"><span>Name</span><input type="text" name="label" placeholder="e.g. Data analyst" required></label>
        <label class="field wide"><span>Job-title keywords that suit it (comma separated)</span><input type="text" name="targets" placeholder="analyst, analytics, business intelligence"></label>
        <label class="field wide"><span>File (PDF, DOCX or TXT, under 5 MB)</span><input type="file" name="file" accept=".pdf,.docx,.txt,.md" required></label>
        <div style="align-self:end"><button class="btn primary">Add &amp; re-rank</button></div></form></div></div>
    <div class="stack">${resumes.map(resumeCard).join('')}</div>
    <div class="card" style="margin-top:16px"><div class="hd"><h2>Tips</h2></div><div class="bd small"><ul class="bullets" style="margin:0">
      <li><b>One resume per target role.</b> The primary one is the default; another wins a job only when it fits clearly better.</li>
      <li><b>Export a text PDF</b> from Word or Google Docs — scanned images can’t be read.</li>
      <li><b>Write skills the way postings do</b> (“PyTorch”, “SQL”, “REST APIs”) so they’re counted as matches.</li>
      <li><b>Check the impact</b> after each upload. If your top matches got worse, revert to the previous version.</li>
      <li>Files stay in <code>data/resume/</code> on this Mac; older versions are kept until you delete them.</li></ul></div></div>`;
  bindRows($('#page-resumes'));
  if ($('#hideImpact')) $('#hideImpact').onclick = () => { lastImpact = null; renderResumes(); };
  $('#addResumeBtn').onclick = () => $('#addResume').classList.toggle('hidden');
  $('#addResumeForm').onsubmit = e => { e.preventDefault(); const f = e.target;
    resumeChange(upload('/resumes', 'POST', { label: f.label.value, targets: f.targets.value, file: f.file.files[0] }), 'Resume added'); };
  $$('[data-replace]').forEach(inp => inp.onchange = () => { if (inp.files[0]) resumeChange(upload(`/resumes/${enc(inp.dataset.replace)}/file`, 'PUT', { file: inp.files[0] }), 'New version uploaded'); });
  $$('[data-primary]').forEach(b => b.onclick = () => resumeChange(put(`/resumes/${enc(b.dataset.primary)}`, { primary: true }), 'Primary resume changed'));
  $$('[data-targets]').forEach(f => f.onsubmit = e => { e.preventDefault();
    resumeChange(put(`/resumes/${enc(f.dataset.targets)}`, { label: f.label.value, targets: f.targets.value.split(',') }), 'Saved'); });
  $$('[data-revert]').forEach(b => b.onclick = () => { if (confirm('Switch back to this version?')) resumeChange(post(`/resumes/${enc(b.dataset.revert)}/revert?version=${enc(b.dataset.v)}`), 'Reverted'); });
  $$('[data-delresume]').forEach(b => b.onclick = () => { if (confirm('Remove this resume? Its files are moved to data/resume/_removed.')) resumeChange(del(`/resumes/${enc(b.dataset.delresume)}`), 'Resume removed'); });
}
function resumeCard(r) {
  const id = esc(r.name);
  return `<div class="card"><div class="hd"><div class="row"><h2>${esc(r.label)}</h2>${r.primary ? '<span class="tag ok">Primary</span>' : ''}</div>
    <div class="row">${r.primary ? '' : `<button class="btn sm" data-primary="${id}">Make primary</button>`}
      <label class="btn sm primary" style="cursor:pointer">Upload new version<input type="file" accept=".pdf,.docx,.txt,.md" data-replace="${id}" hidden></label>
      <a class="btn sm" href="/resume/${enc(r.name)}" target="_blank">Open</a><button class="btn sm ghost danger" data-delresume="${id}">Remove</button></div></div>
    <div class="bd">
      ${r.exists ? `<p class="small mut" style="margin:0 0 10px">${esc(r.file)} · ${r.pages ? `${r.pages} page${r.pages > 1 ? 's' : ''} · ` : ''}${r.words} words · ${r.size_kb} KB${r.uploaded_at ? ` · uploaded ${day(r.uploaded_at)}` : ''}</p>`
        : '<p class="small bad">The file is missing — upload it again.</p>'}
      <h4>Skills found (${r.skills.length})</h4>
      <div class="tags" style="margin:4px 0 12px">${r.skills.map(x => `<span class="tag ok">${esc(x)}</span>`).join('') || '<span class="small mut">none from the skills list</span>'}</div>
      <form class="form-grid" data-targets="${id}">
        <label class="field"><span>Name</span><input type="text" name="label" value="${esc(r.label)}"></label>
        <label class="field wide"><span>Job-title keywords that suit this resume</span><input type="text" name="targets" value="${esc(r.targets.join(', '))}"></label>
        <div class="full"><button class="btn sm">Save name &amp; keywords</button> <span class="tiny faint">Titles containing these get the title-match points.</span></div></form>
      ${r.versions.length ? `<details style="margin-top:12px"><summary class="small"><b>Earlier versions (${r.versions.length})</b></summary><table class="t" style="margin-top:6px"><tbody>
        ${r.versions.map(v => `<tr><td class="small">${esc(v.file)}</td><td class="small mut">${day(v.saved_at)}</td><td><button class="btn sm" data-revert="${id}" data-v="${esc(v.file)}">Use this version</button></td></tr>`).join('')}</tbody></table></details>` : ''}
      <details style="margin-top:8px"><summary class="small"><b>Text JobPrep reads</b></summary><pre class="desc open small" style="margin-top:6px">${esc(r.preview)}…</pre></details>
    </div></div>`;
}

// ---------- PROFILE ----------
const PROFILE_SECTIONS = [
  ['Contact', [['first_name', 'First name'], ['last_name', 'Last name'], ['preferred_name', 'Preferred name'], ['email', 'Email'], ['phone', 'Phone'], ['location', 'Location (City, ST)']]],
  ['Links', [['linkedin', 'LinkedIn URL'], ['github', 'GitHub URL'], ['website', 'Website / portfolio']]],
  ['Graduate program (current)', [['school', 'School', 'wide'], ['degree', 'Degree'], ['major', 'Major / field of study', 'wide'], ['gpa', 'GPA'],
    ['start', 'Started (e.g. August 2026)'], ['graduation', 'Expected graduation (e.g. May 2028)']], 'Forms asking for “School” or “Current education” use this one.'],
  ['Undergraduate', [['undergrad_school', 'School', 'wide'], ['undergrad_degree', 'Degree'], ['undergrad_major', 'Major / minor', 'wide'], ['undergrad_gpa', 'GPA'],
    ['undergrad_start', 'Started'], ['undergrad_graduation', 'Graduated']]],
  ['Work authorization & availability', [['work_authorization', 'How to describe your status', 'wide'], ['availability', 'Availability']]],
];
async function renderProfile() {
  const p = await api('/profile');
  const missing = ['graduation', 'location', 'school', 'linkedin'].filter(k => !p[k]);
  $('#page-profile').innerHTML = `
    <div class="page-head"><div><h1>Profile</h1><p>Used by the packet, Autofill and Simplify-style copying. Stored only in <code>data/profile.yaml</code> on this Mac.</p></div>
      <div class="row"><span id="pMsg" class="small mut"></span><button class="btn primary" id="pSave">Save profile</button></div></div>
    ${missing.length ? `<div class="card" style="margin-bottom:16px"><div class="bd small"><b class="warn">Still empty:</b> ${missing.map(k => esc(k.replace('_', ' '))).join(', ')} — Autofill will leave these for you.</div></div>` : ''}
    <div class="stack">
      ${PROFILE_SECTIONS.map(([t, fs, note]) => `<div class="card"><div class="hd"><h2>${t}</h2>${note ? `<span class="small mut">${note}</span>` : ''}</div><div class="bd form-grid">
        ${fs.map(([k, l, w]) => `<label class="field ${w || ''}"><span>${l}</span><input type="text" data-p="${k}" value="${esc(p[k])}"></label>`).join('')}</div></div>`).join('')}
      <div class="card"><div class="hd"><h2>Companies you’ve worked at</h2><span class="small mut">“Have you worked here before?” becomes Yes for these</span></div>
        <div class="bd"><input type="text" data-p="worked_at" value="${esc(p.worked_at.join(', '))}" placeholder="e.g. F5, F5 Networks"></div></div>
      <div class="card"><div class="hd"><h2>Canned answers</h2><button class="btn sm" id="addAns">+ Add answer</button></div><div class="bd">
        <p class="small mut" style="margin:0 0 8px">“Matches” are phrases that identify the question on a form (Autofill uses them). <code>{company}</code> becomes the employer’s name.</p>
        <table class="t"><thead><tr><th style="width:36%">Question</th><th style="width:20%">Answer</th><th>Matches (comma separated)</th><th></th></tr></thead><tbody>
        ${p.answers.map((a, i) => `<tr><td><input type="text" data-a="${i}" data-ak="question" value="${esc(a.question)}">${a.note ? `<div class="tiny faint" style="margin-top:3px">${esc(a.note)}</div>` : ''}</td>
          <td><input type="text" data-a="${i}" data-ak="answer" value="${esc(a.answer)}"></td>
          <td><input type="text" data-a="${i}" data-ak="patterns" value="${esc(a.patterns.join(', '))}"></td>
          <td><button class="btn sm ghost" data-rm="${i}" title="Remove">✕</button></td></tr>`).join('')}</tbody></table></div></div>
    </div>`;
  const collect = () => {
    const out = { ...p };
    $$('[data-p]').forEach(el => out[el.dataset.p] = el.dataset.p === 'worked_at' ? el.value.split(',').map(x => x.trim()).filter(Boolean) : el.value);
    out.answers = p.answers.map((a, i) => ({ ...a, ...Object.fromEntries($$(`[data-a="${i}"]`).map(el => [el.dataset.ak, el.dataset.ak === 'patterns' ? el.value.split(',').map(x => x.trim().toLowerCase()).filter(Boolean) : el.value])) }));
    return out;
  };
  $('#pSave').onclick = async () => { try { await put('/profile', collect()); toast('Profile saved'); renderProfile(); } catch (e) { $('#pMsg').textContent = e.message; } };
  $('#addAns').onclick = async () => { const q2 = collect(); q2.answers.push({ question: 'New question', answer: '', patterns: [], note: '' }); await put('/profile', q2); renderProfile(); };
  $$('[data-rm]').forEach(b => b.onclick = async () => { const q2 = collect(); q2.answers.splice(Number(b.dataset.rm), 1); await put('/profile', q2); renderProfile(); });
}

// ---------- SETTINGS ----------
async function renderSettings() {
  if (!Object.keys(S).length) await loadSettings();
  const st = await api('/status');
  $('#page-settings').innerHTML = `
    <div class="page-head"><div><h1>Settings</h1><p>Filters and ranking live on the Jobs page. These control refresh, alerts and upkeep. Changes save automatically.</p></div></div>
    <div class="settings-grid">
      ${SETTINGS_GROUPS.map(([t, d, fs]) => `<div class="card"><div class="hd"><h2>${t}</h2></div><div class="bd"><p class="small mut" style="margin:0 0 6px">${d}</p>${fs.map(fieldHtml).join('')}
        ${t === 'Maintenance' ? `<div class="row" style="margin-top:10px"><button class="btn sm" id="backupNow">Back up now</button>
          <span class="small mut">${st.maintenance ? `last cleanup ${ago(st.maintenance.last_run)}${st.maintenance.pruned != null ? ` · removed ${st.maintenance.pruned} old postings` : ''}` : ''}</span></div>` : ''}</div></div>`).join('')}
      <div class="card"><div class="hd"><h2>Run at login</h2></div><div class="bd small">
        <p style="margin:0">Keeps JobPrep running in the background so refreshes, alerts and the digest happen even when no terminal is open.</p>
        <ol class="steps"><li>Stop the server you started in Terminal (Ctrl-C).</li>
          <li>Run <code class="copy" data-copy="scripts/install_service.sh">scripts/install_service.sh</code> from the project folder.</li>
          <li>Open <a href="http://127.0.0.1:8000">127.0.0.1:8000</a> any time. Undo with <code>scripts/uninstall_service.sh</code>.</li></ol></div></div>
      <div class="card"><div class="hd"><h2>Data on this Mac</h2></div><div class="bd small"><div class="kv">
        <span>Jobs database</span><span><code>jobs.db</code> (postings, embeddings, tracker, emails)</span>
        <span>Personal files</span><span><code>data/</code> — resumes, profile, settings, backups, model</span>
        <span>Leaves the Mac</span><span>Only requests for public job listings, the pages you open, and Gmail reads if connected.</span></div></div></div>
    </div>`;
  bindFields($('#page-settings'), async () => { try { const mode = S.schedule; S = await put('/settings', S); toast('Saved'); if (mode !== st.schedule?.mode) renderSettings(); } catch (e) { toast(e.message); } });
  $('#page-settings .card .hd h2').insertAdjacentHTML('afterend', '<button class="btn sm" id="runMorning">Run morning routine now</button>');
  $('#runMorning').onclick = async () => { const r = await post('/refresh?source=morning'); toast(r.started ? 'Morning routine started — takes a few minutes' : 'Already running'); };
  if ($('#backupNow')) $('#backupNow').onclick = async () => { const r = await post('/maintenance/backup'); toast(`Backed up to ${r.path.split('/').slice(-2).join('/')}`); };
}

// ---------- boot ----------
(async () => {
  await loadSettings().catch(e => toast(e.message));
  go(location.hash.slice(1) || 'home');
  loadStatus(); loadInboxCount();
  setInterval(() => { loadStatus(); loadInboxCount(); }, 60 * 1000);
})();
