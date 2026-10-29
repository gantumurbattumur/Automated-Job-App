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
  ['Location', true, [
    { k: 'near_location', t: 'place', l: 'Near', h: 'A city, e.g. “Los Angeles, CA” or “LA”. Empty = anywhere.' },
    { k: 'near_radius_miles', t: 'select', l: 'Within', num: 1, o: { 10: '10 miles', 25: '25 miles', 50: '50 miles', 100: '100 miles', 200: '200 miles', 500: '500 miles' } },
    { k: 'near_include_remote', t: 'bool', l: 'Include remote jobs' },
    { k: 'near_keep_unknown', t: 'bool', l: 'Keep jobs whose location can’t be mapped (“Multiple locations”)' },
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
const PAGES = { home: renderHome, jobs: renderJobsPage, tracker: renderTracker, inbox: renderInbox, resumes: renderResumes, tailor: renderTailor, settings: renderSettings };
function go(page) {
  page = PAGES[page] ? page : 'home';
  for (const p in PAGES) $(`#page-${p}`)?.classList.toggle('hidden', p !== page);  // tolerate a stale index.html
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
  $('#srcStatus').innerHTML = chip || [['simplify', 'SimplifyJobs'], ['ats', 'Company boards']].map(([k, l]) => {
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

// ---------- HOME: today's plan (Jobs is for browsing everything) ----------
async function renderHome() {
  if (!$('#page-home').innerHTML.trim()) $('#page-home').innerHTML = LOADING;
  const [h, t] = await Promise.all([api('/home'), api('/tailor').catch(() => ({ items: [] }))]);
  const s = h.stats, by = s.by_status;
  const picks = h.top.slice(0, 6);
  const toTailor = t.items.filter(i => !i.suggestions || i.accepted < i.suggestions);
  const todo = [
    ...h.follow_ups.map(a => ({ go: 'tracker', tag: ['warn', 'Follow up'], text: `${a.company} — ${a.title}`, meta: a.follow_up })),
    h.inbox ? { go: 'inbox', tag: ['ac', 'Inbox'], text: `${h.inbox} job email${h.inbox > 1 ? 's' : ''} to review` } : null,
    ...h.closing_soon.slice(0, 3).map(j => ({ open: j.id, tag: ['warn', `${Math.ceil(j.days_left)}d left`], text: `${j.title} · ${j.company}` })),
    !h.gmail ? { go: 'inbox', tag: ['', 'Setup'], text: 'Connect Gmail so replies update your tracker' } : null,
  ].filter(Boolean);
  const today = new Date().toLocaleDateString([], { weekday: 'long', month: 'short', day: 'numeric' });
  const stages = [['saved', 'Saved'], ['applied', 'Applied'], ['oa', 'Assessment'], ['interview', 'Interview'], ['offer', 'Offer'], ['rejected', 'Rejected']];
  const days = s.applied_by_day || [], peak = Math.max(1, ...days);
  const dayName = i => new Date(Date.now() - (6 - i) * 864e5).toLocaleDateString([], { weekday: 'narrow' });
  const row = x => `<div class="list-row" ${x.go ? `data-go="${x.go}"` : `data-open="${esc(x.open)}"`}><span class="tag ${x.tag[0]}">${esc(x.tag[1])}</span><span class="grow small">${esc(x.text)}</span>${x.meta ? `<span class="tiny mut">${esc(x.meta)}</span>` : ''}</div>`;
  $('#page-home').innerHTML = `
    <div class="hero"><div><div class="small mut">${today}</div>
      <h1>${h.new_today ? `${h.new_today} new match${h.new_today > 1 ? 'es' : ''} today` : 'No new matches yet today'}</h1>
      <p>Your plan: review today’s picks, tailor the best ones, apply.</p></div>
      <button class="btn" data-go="jobs">Browse all ${h.shown.toLocaleString()} matches →</button></div>
    <div class="card funnel">
      <div class="fn-stages">${stages.map(([k, l]) => `<div class="fn-stage ${k}" data-go="tracker"><div class="v">${by[k] || 0}</div><div class="l">${l}</div></div>`).join('')}</div>
      <div class="fn-week" title="Applications per day, last 7 days"><div class="fn-bars">${days.map((n, i) => `<div class="bar-d ${i === 6 ? 'today' : ''}" title="${n} applied"><i style="height:${Math.round(n / peak * 100)}%"></i><span>${dayName(i)}</span></div>`).join('')}</div>
        <div class="small"><b>${s.applied_last_7d}</b> applied this week · <b>${Math.round(s.response_rate * 100)}%</b> response rate</div></div>
    </div>
    <div class="home3">
      <div><div class="sec-hd"><h2>Today’s picks</h2><span class="small mut">best new matches for your resume</span></div>
        ${picks.length ? `<div class="picks">${picks.map(pickCard).join('')}</div>` : '<div class="card"><div class="empty">Nothing new in the last 24 hours. The next run is tomorrow morning — meanwhile, browse all matches.</div></div>'}</div>
      <div class="side">
        <div class="card"><div class="hd"><h2>To do</h2></div><div>${todo.length ? todo.map(row).join('') : '<div class="empty small">All caught up.</div>'}</div></div>
        <div class="card"><div class="hd"><h2>Tailoring</h2><a href="#tailor" class="small">${t.items.length}/${t.max || 10} →</a></div><div>
          ${t.items.length ? t.items.slice(0, 5).map(i => ({ ...i, score: i.score ?? i.baseline ?? 0 })).map(i => `<div class="list-row" data-go="tailor"><span class="grow small"><b>${esc(i.company)}</b> · ${esc(i.title)}
            <div class="meter"><i class="${i.score >= 75 ? 'ok' : i.score >= 55 ? 'mid' : ''}" style="width:${Math.round(i.score)}%"></i></div></span>
            <span class="small"><b>${Math.round(i.score)}%</b></span></div>`).join('') + (toTailor.length ? `<div class="tiny mut" style="padding:6px 14px">${toTailor.length} still need suggestions reviewed</div>` : '')
          : '<div class="empty small">Click ✎ Tailor on a pick to match your resume to it.</div>'}</div></div>
        <div class="card"><div class="hd"><h2>Recent activity</h2><a href="#tracker" class="small">Tracker →</a></div><div>
          ${h.recent.length ? h.recent.slice(0, 5).map(a => `<div class="list-row" data-go="tracker"><span class="tag ${a.status === 'rejected' ? 'bad' : ['oa', 'interview', 'offer'].includes(a.status) ? 'ok' : 'ac'}">${SLABEL[a.status]}</span><span class="grow small">${esc(a.company)} · ${esc(a.title)}</span><span class="tiny mut">${ago(a.updated_at || a.opened_at)}</span></div>`).join('')
          : '<div class="empty small">Nothing tracked yet.</div>'}</div></div>
      </div>
    </div>`;
  bindRows($('#page-home'));
  $$('#page-home [data-hact]').forEach(b => b.onclick = async e => {
    e.stopPropagation();
    const j = picks.find(x => x.id === b.dataset.id);
    if (b.dataset.hact === 'apply') return act('apply', j);
    try { const r = await post('/tailor', { job_id: j.id }); tailorSel = r.id; go('tailor'); } catch (err) { toast(err.message); }
  });
}
function pickCard(j) {
  const why = (j.skills_matched || []).slice(0, 4), miss = (j.skills_missing || []).slice(0, 2);
  return `<div class="card pick"><div class="row between"><span class="score ${scoreCls(j.score)}">${Math.round(j.score)}</span>
      <span class="tiny mut">${ago(j.posted_at)} · ${esc(laneLabel(j.best_lane))} resume</span></div>
    <div class="pk-t" data-open="${esc(j.id)}" title="Open in Jobs"><b>${esc(j.title)}</b></div>
    <div class="small mut">${esc(j.company)} · ${esc(shortLoc(j.location))}</div>
    ${why.length ? `<div class="small pk-why"><span class="ok">✓</span> ${why.map(esc).join(', ')}${miss.length ? ` <span class="mut">· missing ${miss.map(esc).join(', ')}</span>` : ''}</div>` : ''}
    <div class="tags">${cptTagIfKnown(j)}${j.days_left != null && j.days_left >= 0 ? `<span class="tag warn">${Math.ceil(j.days_left)}d left</span>` : ''}${j.hourly ? `<span class="tag">$${j.hourly[1]}/hr</span>` : ''}</div>
    <div class="acts"><button class="btn sm" data-hact="tailor" data-id="${esc(j.id)}">✎ Tailor</button><button class="btn sm primary" data-hact="apply" data-id="${esc(j.id)}">Apply ↗</button></div></div>`;
}
const LOADING = '<div class="card"><div class="empty"><span class="spin"></span> Loading…</div></div>';
const kpi = (v, l) => `<div class="card kpi"><div class="v">${v}</div><div class="l">${l}</div></div>`;
function sourceList(st) {
  return [['morning', 'Morning run (everything)'], ['simplify', 'SimplifyJobs internship list'], ['ats', 'Company job boards'], ['gmail', 'Gmail scan'], ['digest', 'Morning digest'], ['maintenance', 'Cleanup & backup']]
    .map(([k, l]) => { const v = st[k]; return `<div class="row between" style="padding:3px 0"><span>${l}</span><span class="${v?.error ? 'bad' : 'mut'}">${v ? (v.running ? 'running…' : `${ago(v.last_run)}${v.error ? ' · error' : ''}`) : 'not yet'}</span></div>`; }).join('');
}
function jobRow(j) {
  return `<div class="list-row" data-open="${esc(j.id)}"><span class="score ${scoreCls(j.score)}">${Math.round(j.score)}</span>
    <span class="grow"><b>${esc(j.title)}</b><br><span class="small mut">${esc(j.company)} · ${esc(shortLoc(j.location))}${j.distance_mi != null ? ` (${j.distance_mi} mi)` : ''} · posted ${ago(j.posted_at)}</span></span>
    ${cptTag(j)}${j.days_left != null && j.days_left >= 0 ? `<span class="tag warn">${Math.ceil(j.days_left)}d left</span>` : ''}</div>`;
}
function bindRows(root) {
  $$('[data-go]', root).forEach(b => b.onclick = () => go(b.dataset.go));
  $$('[data-open]', root).forEach(b => b.onclick = () => { selected = b.dataset.open; openOnLoad = true; go('jobs'); });
}
const shortLoc = l => {  // "Pittsburgh PA, 15222, PAP, Penn Avenue Place, United States of America" -> "Pittsburgh PA"
  const parts = String(l || '').split(/;|•/).map(x => x.trim()).filter(Boolean);
  const first = (parts[0] || '').split(',').map(x => x.trim()).filter(x => x && !/^\d{5}$|^(united states( of america)?|usa|us)$/i.test(x)).slice(0, 2).join(', ');
  return parts.length > 1 ? `${first} +${parts.length - 1}` : (first || 'Location not listed');
};
// "CPT unclear" is the norm, so lists only show the tag when it says something
const cptTagIfKnown = j => j.cpt !== 'unclear' || j.cpt_label?.startsWith('likely') ? cptTag(j) : '';
function cptTag(j) {
  if (j.cpt_label?.startsWith('likely')) return `<span class="tag ok" title="${esc(j.cpt_label)}">Likely CPT OK</span>`;
  if (j.cpt_label?.startsWith('rejected')) return `<span class="tag bad">Rejected you over CPT</span>`;
  const [c, l] = CPT[j.cpt] || ['', j.cpt]; return `<span class="tag ${c}" title="${esc(j.cpt_label || '')}">${l}</span>`;
}

// ---------- JOBS ----------
async function renderJobsPage() {
  if (!openOnLoad) $('#page-jobs').classList.remove('show-detail');  // the nav opens the list
  if (!Object.keys(S).length) await loadSettings();
  renderQuickbar();
  renderFilters();
  await loadJobs();
}
// the five filters people actually change; everything else lives in the "More filters" drawer
const ROLE_SETS = { tech: ['ai_ml', 'data_science', 'data_eng', 'swe', 'research', 'quant'], ai: ['ai_ml', 'data_science', 'research'],
  swe: ['swe'], data: ['data_science', 'data_eng'], any: [] };
const CPT_SETS = { ok: ['friendly', 'unclear'], friendly: ['friendly'], all: ['friendly', 'unclear', 'blocked'] };
const QUICK_KEYS = ['posted_within_days', 'categories', 'seasons', 'near_location', 'near_radius_miles', 'cpt_allow'];
const sameSet = (a, b) => a.length === b.length && a.every(x => b.includes(x));
function renderQuickbar() {
  const role = Object.keys(ROLE_SETS).find(k => sameSet(ROLE_SETS[k], S.categories)) || 'custom';
  const cpt = Object.keys(CPT_SETS).find(k => sameSet(CPT_SETS[k], S.cpt_allow)) || 'custom';
  const season = S.seasons.length === 1 ? S.seasons[0] : S.seasons.length ? 'custom' : '';
  const opt = (o, v) => Object.entries(o).map(([k, l]) => `<option value="${esc(k)}" ${String(v) === k ? 'selected' : ''}>${esc(l)}</option>`).join('');
  $('#quickbar').innerHTML = `
    <label class="field"><span>Posted</span><select data-q="posted">${opt({ 0: 'Any time', 1: 'Last 24 hours', 3: 'Last 3 days', 7: 'Last week', 30: 'Last month' }, S.posted_within_days)}</select></label>
    <label class="field"><span>Role</span><select data-q="role">${opt({ tech: 'All technical', ai: 'AI / ML / data science', swe: 'Software', data: 'Data', any: 'Any role', ...(role === 'custom' ? { custom: 'Custom' } : {}) }, role)}</select></label>
    <label class="field"><span>Season</span><select data-q="season">${opt({ '': 'Any season', ...Object.fromEntries(SEASONS.map(x => [x, x.replace(/^\w/, c => c.toUpperCase())])), ...(season === 'custom' ? { custom: 'Custom' } : {}) }, season)}</select></label>
    <label class="field" style="min-width:170px"><span>Near</span><input type="text" data-q="near" value="${esc(S.near_location)}" placeholder="Anywhere" list="dl-qnear" autocomplete="off"><datalist id="dl-qnear"></datalist></label>
    <label class="field" style="min-width:100px"><span>Within</span><select data-q="radius" ${S.near_location ? '' : 'disabled'}>${opt({ 10: '10 mi', 25: '25 mi', 50: '50 mi', 100: '100 mi', 200: '200 mi', 500: '500 mi' }, S.near_radius_miles)}</select></label>
    <label class="field"><span>CPT</span><select data-q="cpt">${opt({ ok: 'Hide “no CPT”', friendly: 'Says CPT OK only', all: 'Show all', ...(cpt === 'custom' ? { custom: 'Custom' } : {}) }, cpt)}</select></label>
    <button class="btn" id="moreFilters">More filters</button>`;
  const set = (k, v) => { setk(k, v); saveFilters(); };
  $('[data-q=posted]').onchange = e => set('posted_within_days', Number(e.target.value));
  $('[data-q=role]').onchange = e => e.target.value !== 'custom' && set('categories', [...ROLE_SETS[e.target.value]]);
  $('[data-q=season]').onchange = e => e.target.value !== 'custom' && set('seasons', e.target.value ? [e.target.value] : []);
  $('[data-q=radius]').onchange = e => set('near_radius_miles', Number(e.target.value));
  $('[data-q=cpt]').onchange = e => e.target.value !== 'custom' && set('cpt_allow', [...CPT_SETS[e.target.value]]);
  const near = $('[data-q=near]'); let tm;
  near.onchange = () => set('near_location', near.value.trim());
  near.oninput = () => { clearTimeout(tm); tm = setTimeout(async () => { $('#dl-qnear').innerHTML = (await api(`/geo/suggest?q=${enc(near.value)}`).catch(() => [])).map(o => `<option value="${esc(o)}">`).join(''); }, 200); };
  $('#moreFilters').onclick = () => $('#drawer').classList.remove('hidden');
}
document.addEventListener('click', e => { if (e.target.closest('#drawerClose')) $('#drawer').classList.add('hidden'); });
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
  if (f.t === 'place') h = `<input type="text" data-k="${f.k}" value="${esc(v)}" list="dl-${f.k}" placeholder="Anywhere" autocomplete="off"><datalist id="dl-${f.k}"></datalist>`;
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
    if (f.t === 'place') { let tm; el.addEventListener('input', () => { clearTimeout(tm); tm = setTimeout(async () => {
      const opts = await api(`/geo/suggest?q=${enc(el.value)}`).catch(() => []);
      el.nextElementSibling.innerHTML = opts.map(o => `<option value="${esc(o)}">`).join(''); }, 200); }); }
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
    try { S = await put('/settings', S); if ($('#preset')) $('#preset').value = ''; renderQuickbar(); renderFilters(); renderActiveFilters(); loadJobs(); }
    catch (e) { toast(e.message); S = await api('/settings'); renderQuickbar(); renderFilters(); }  // put back what's actually saved
  }, 300);
}
function renderActiveFilters() {
  const chips = [];
  for (const [, , fs] of FILTERS) for (const f of fs) {
    if (f.t === 'range' || QUICK_KEYS.includes(f.k) || ['freshness_half_life_days', 'new_badge_hours', 'allow_boost', 'stale_days', 'h1b_min_approvals'].includes(f.k)) continue;
    const v = getk(f.k), d = f.k.split('.').reduce((o, p) => o?.[p], DEFAULTS);
    if (JSON.stringify(v) === JSON.stringify(d)) continue;
    let txt = Array.isArray(v) ? (v.length ? v.map(x => f.o?.[x] || x).join(', ') : 'any') : f.t === 'bool' ? (v ? 'on' : 'off') : (f.o?.[v] || v);
    chips.push(`<span class="tag ac" title="${esc(f.l)}: ${esc(txt)} — click ✕ to reset">${esc(f.l.length > 28 ? f.l.slice(0, 26) + '…' : f.l)} <a href="#" data-reset="${f.k}">✕</a></span>`);
  }
  $('#activeFilters').innerHTML = chips.length ? `<span class="tiny mut">Also filtering by:</span> ${chips.join('')}` : '';
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
    relevance: (a, b) => b.relevance - a.relevance, deadline: (a, b) => (a.days_left ?? 1e9) - (b.days_left ?? 1e9),
    nearest: (a, b) => (a.distance_mi ?? 1e9) - (b.distance_mi ?? 1e9) }[sort] || ((a, b) => b.score - a.score);
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
  $$('#list .job').forEach(el => el.onclick = () => { select(el.dataset.id); showDetail(true); });
  if (!rows.find(r => r.id === selected)) selected = rows[0]?.id || null;
  if (selected) { select(selected, false); if (openOnLoad) showDetail(true); } else $('#detail').innerHTML = '<div class="card"><div class="empty">Select a job to see the details.</div></div>';
}
// narrow screens show one pane at a time: the list, or the selected job with a back button
let openOnLoad = false;
function showDetail(on) {
  openOnLoad = false;
  $('#page-jobs').classList.toggle('show-detail', on);
  if (on && matchMedia('(max-width: 1100px)').matches) $('#main').scrollTo(0, 0);
}
document.addEventListener('click', e => { if (e.target.closest('#detailBack')) showDetail(false); });
function listItem(j) {
  const hid = j.hidden_reasons.length, why = (j.skills_matched || []).slice(0, 4);
  const tags = [j.status ? `<span class="tag ac">${SLABEL[j.status]}</span>` : '',
    cptTagIfKnown(j),
    j.days_left != null && j.days_left >= 0 && j.days_left <= 14 ? `<span class="tag warn">${Math.ceil(j.days_left)}d left</span>` : '',
    j.hourly ? `<span class="tag">$${j.hourly[1]}/hr</span>` : '',
    j.work_mode && j.work_mode !== 'onsite' ? `<span class="tag">${esc(j.work_mode)}</span>` : '',
    hid ? `<span class="tag bad">hidden: ${esc(j.hidden_reasons[0])}${hid > 1 ? ` +${hid - 1}` : ''}</span>` : ''].join('');
  return `<div class="job ${j.id === selected ? 'sel' : ''} ${hid ? 'is-hidden' : ''}" data-id="${esc(j.id)}">
    <div class="t">${isUnseen(j) ? '<span class="unseen-dot" title="New since your last visit"></span>' : ''}${esc(j.title)}</div>
    <div class="score ${scoreCls(j.score)}" title="Score out of 100">${Math.round(j.score)}</div>
    <div class="meta"><b class="co">${esc(j.company)}</b> · ${esc(shortLoc(j.location))}${j.distance_mi != null ? ` · <b>${j.distance_mi} mi</b>` : ''} · ${ago(j.posted_at)}</div>
    ${why.length ? `<div class="why">✓ ${why.map(esc).join(' · ')}</div>` : ''}
    ${tags ? `<div class="tags">${tags}</div>` : ''}</div>`;
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
  const chips = [cptTag(j),
    j.h1b_approvals ? `<span class="tag" title="USCIS H-1B approvals FY2025–26">${j.h1b_approvals.toLocaleString()} H-1B</span>` : '',
    j.hourly ? `<span class="tag">$${j.hourly[0]}${j.hourly[1] !== j.hourly[0] ? `–${j.hourly[1]}` : ''}/hr</span>` : '',
    j.work_mode ? `<span class="tag">${esc(j.work_mode)}</span>` : '', ...(j.seasons || []).map(x => `<span class="tag">${esc(x)}</span>`),
    j.distance_mi != null ? `<span class="tag">${j.distance_mi} mi away</span>` : '',
    j.deadline ? `<span class="tag ${j.days_left < 7 ? 'warn' : ''}">apply by ${dateOnly(j.deadline)}</span>` : '',
    ...(j.auth || []).map(a => `<span class="tag bad">requires ${esc(a.replace('_', ' '))}</span>`),
    ...(j.education || []).map(e => `<span class="tag warn">${esc(e.replace(/_/g, ' '))}</span>`),
    ...(j.warnings || []).filter(w => !(w.startsWith("Master's") && (j.education || []).includes('masters_not_listed'))).map(w => `<span class="tag warn">${esc(w)}</span>`)].filter(Boolean).join('');
  $('#detail').innerHTML = `
    <button class="btn sm back-btn" id="detailBack">← All jobs</button>
    <div class="card"><div class="bd">
      <div class="row between"><span class="small mut">${esc(j.company)}</span><span class="score ${scoreCls(j.score)}" style="grid-row:auto" title="Match score">${Math.round(j.score ?? 0)}</span></div>
      <div class="title">${esc(j.title)}</div>
      <div class="small mut" title="${esc(j.location)}">${esc(shortLoc(j.location))} · posted ${ago(j.posted_at)} · ${esc(SRC[j.source] || j.source)}${j.status ? ` · <b>${SLABEL[j.status]}</b>` : ''}</div>
      <div class="glance">${chips}</div>
      <div class="row" style="margin-top:14px">
        <button class="btn primary" data-act="apply" title="Open the posting (shortcut: a)">Apply ↗</button>
        <button class="btn" data-act="tailor" title="Match your resume to this job">✎ Tailor resume</button>
        <div class="icon-btns"><button class="btn sm ghost" data-act="saved" title="Save to tracker (s)">☆ Save</button>
          <button class="btn sm ghost" data-act="applied" title="Mark applied">✓ Applied</button>
          <button class="btn sm ghost danger" data-act="dismissed" title="Not interested — hides it (x)">✕</button></div></div>
      ${j.hidden_reasons?.length ? `<p class="small bad" style="margin:10px 0 0">Hidden by your filters: ${j.hidden_reasons.map(esc).join(' · ')}</p>` : ''}
      <details style="margin-top:12px"><summary class="small mut">Why ${Math.round(j.score ?? 0)}? Resume match ${Math.round((j.relevance ?? 0) * 100)}% (${esc(laneLabel(j.best_lane))} resume)</summary>
        <div class="bars" style="margin-top:8px">${Object.entries(parts).map(([k, v]) => `<i style="width:${100 * v / total}%;background:var(${PARTS[k]?.[1] || '--faint'})" title="${PARTS[k]?.[0] || k}: ${v}"></i>`).join('')}</div>
        <div class="legend">${Object.entries(parts).map(([k, v]) => `<span><span class="sw" style="background:var(${PARTS[k]?.[1] || '--faint'})"></span>${PARTS[k]?.[0] || k}: <b>${v}</b></span>`).join('')}</div>
        <p class="tiny mut" style="margin:6px 0 0">Resume match = meaning ${Math.round((rp.semantic ?? 0) * 100)}% · skills ${Math.round((rp.skills ?? 0) * 100)}% · title ${Math.round((rp.title ?? 0) * 100)}%. ${j.cpt_label ? `CPT: ${esc(j.cpt_label)}.` : ''}</p>
      </details>
    </div></div>
    <div class="card" id="fitCard"><div class="hd"><h3>Resume fit</h3><button class="btn sm" id="fitToggle">${fitOpen ? 'Hide map' : 'Where to add keywords'}</button></div><div class="bd">
      <div class="small">${(j.skills_matched || []).length ? `<span class="ok">✓ ${j.skills_matched.slice(0, 8).map(esc).join(', ')}</span>` : ''}
        ${(j.skills_missing || []).length ? `<div style="margin-top:4px">Missing: ${j.skills_missing.slice(0, 10).map(x => `<span class="tag bad">${esc(x)}</span>`).join(' ')}</div>` : ''}</div>
      <div id="fitBody">${fitOpen ? '<div class="small mut" style="margin-top:10px"><span class="spin"></span> Mapping your resume…</div>' : ''}</div></div></div>
    <div class="card"><div class="hd"><h3>Description</h3>${full?.description ? '<button class="btn sm ghost" id="moreDesc">Show all</button>' : ''}</div><div class="bd">
      ${full ? (full.description ? `<div class="desc" id="desc">${esc(full.description)}</div>` : '<p class="mut small" style="margin:0">Only the title is known so far — open the posting to read it.</p>') : '<span class="spin"></span>'}
      ${j.also?.length ? `<p class="small" style="margin:10px 0 0">Also listed on: ${j.also.map(a => `<a href="${esc(a.url)}" target="_blank" rel="noopener">${esc(SRC[a.source] || a.source)}</a>`).join(' · ')}</p>` : ''}
    </div></div>`;
  $$('#detail [data-act]').forEach(b => b.onclick = () => act(b.dataset.act, j, b));
  $('#fitToggle').onclick = () => { fitOpen = !fitOpen; store.set('fitOpen', fitOpen); renderDetail(j, full); };
  if (fitOpen && full) loadFit(j);
  if ($('#moreDesc')) $('#moreDesc').onclick = () => { $('#desc').classList.toggle('open'); $('#moreDesc').textContent = $('#desc').classList.contains('open') ? 'Show less' : 'Show all'; };
}
let fitOpen = store.get('fitOpen', false);
async function loadFit(j) {
  const c = detailCache[j.id] || {};
  try { c.fit = c.fit || await api(`/jobs/${enc(j.id)}/fit`); } catch (e) { if (selected === j.id && $('#fitBody')) $('#fitBody').innerHTML = `<p class="small bad">${esc(e.message)}</p>`; return; }
  if (selected !== j.id || !$('#fitBody')) return;
  const f = c.fit;
  $('#fitBody').innerHTML = `<div class="row between" style="margin:12px 0 8px"><span class="small"><b>ATS-style match ${Math.round(f.score)}%</b> · ${f.keywords.have} of ${f.keywords.total} job keywords · ${esc(laneLabel(f.lane))} resume</span>
      <button class="btn sm primary" data-act="tailor">✎ Tailor with Claude</button></div>
    ${f.has_description ? resumeMapHtml(f.map) : '<p class="small mut">No description saved for this job yet, so there is nothing to map.</p>'}`;
  $$('#fitBody [data-act]').forEach(b => b.onclick = () => act('tailor', j));
}
async function setStatus(id, status, extra = {}) { await put(`/applications/${enc(id)}`, { status, ...extra }); }
async function act(what, j, btn) {
  if (what === 'tailor') {
    try { const r = await post('/tailor', { job_id: j.id }); tailorSel = r.id; go('tailor'); }
    catch (e) { toast(e.message); }
    return;
  }
  if (what === 'apply') { window.open(j.url, '_blank', 'noopener'); await setStatus(j.id, 'opened', { source: 'apply_prompt' }); pending = j; return; }
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
  if (e.key === 'Escape') return showDetail(false);
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
  try { const r = await promise; lastImpact = r.impact; detailCache = {}; await loadSettings(); toast(msg); }
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

// ---------- TAILOR ----------
let tailorSel = null;
const PART_LABEL = { hard_skills: 'Hard skills (keywords)', requirements: 'Requirements backed by a bullet', title: 'Job title',
  education: 'Education', soft_skills: 'Soft skills' };
const STATUS_TAG = { met: ['ok', '✓ met'], partial: ['warn', '◐ partial'], missing: ['bad', '✗ missing'] };
const meter = (v, target = 90) => `<div class="meter"><i style="width:${Math.min(v, 100)}%" class="${v >= target ? 'ok' : v >= 70 ? 'mid' : ''}"></i><b style="left:${target}%"></b></div>`;

let tailorTab = 'match', showAdd = false;
async function renderTailor() {
  if (!$('#page-tailor').innerHTML.trim()) $('#page-tailor').innerHTML = LOADING;
  const { items, max } = await api('/tailor');
  if (!items.find(i => i.id === tailorSel)) tailorSel = items[0]?.id || null;
  $('#page-tailor').innerHTML = `
    <div class="hero"><div><h1>Tailor your resume</h1><p>See what each job asks for that your resume doesn’t show, then get truthful bullet suggestions from Claude.</p></div>
      <div class="row"><span class="tag ${items.length >= max ? 'warn' : ''}">${items.length} / ${max} jobs</span><button class="btn primary" id="tadd" ${items.length >= max ? 'disabled' : ''}>+ Add a job</button></div></div>
    <div class="card ${showAdd ? '' : 'hidden'}" id="taddcard" style="margin-bottom:14px"><div class="bd">
      <p class="small mut" style="margin:0 0 8px">Tip: on the Jobs page, <b>✎ Tailor resume</b> adds a job in one click. For anything else, paste it here.</p>
      <form id="tpaste" class="form-grid">
        <input type="text" name="company" placeholder="Company" required><input type="text" name="title" placeholder="Job title" required>
        <input type="text" name="url" placeholder="Posting link (optional)">
        <textarea name="jd" rows="5" class="full" placeholder="Paste the full job description" required></textarea>
        <div class="full row"><button class="btn primary">Add &amp; analyze</button><button type="button" class="btn ghost" id="tcancel">Cancel</button></div></form></div></div>
    ${items.length ? `<div class="tailor"><div class="stack" id="tlist">${items.map(tailorCard).join('')}</div><div id="tdetail"><div class="card"><div class="empty"><span class="spin"></span> Analyzing…</div></div></div></div>`
      : `<div class="card"><div class="empty">No jobs yet. Use <b>✎ Tailor resume</b> on a job, or <b>+ Add a job</b> to paste a description.</div></div>`}`;
  $('#tadd').onclick = () => { showAdd = !showAdd; $('#taddcard').classList.toggle('hidden', !showAdd); };
  $('#tcancel').onclick = () => { showAdd = false; $('#taddcard').classList.add('hidden'); };
  $('#tpaste').onsubmit = async e => { e.preventDefault(); const f = e.target;
    try { const r = await post('/tailor', { company: f.company.value, title: f.title.value, url: f.url.value, jd_text: f.jd.value }); tailorSel = r.id; showAdd = false; tailorTab = 'match'; renderTailor(); }
    catch (err) { toast(err.message); } };
  bindTailorList();
  if (tailorSel) renderTailorDetail(await api(`/tailor/${enc(tailorSel)}`));
}
function tailorCard(i) {
  const v = i.with_accepted ?? i.score;
  return `<div class="card titem ${i.id === tailorSel ? 'sel' : ''}" data-tsel="${esc(i.id)}"><div class="bd">
    <button class="btn sm ghost tdel" data-tdel="${esc(i.id)}" title="Remove">✕</button><b class="ttl">${esc(i.title)}</b>
    <div class="small mut">${esc(i.company)}</div>
    ${i.error ? `<p class="small bad">${esc(i.error)}</p>` : `<div class="row between small" style="margin-top:6px"><span><b>${Math.round(i.score)}%</b>${i.with_accepted != null && i.with_accepted !== i.score ? ` → <b class="ok">${Math.round(i.with_accepted)}%</b>` : ''}</span>
      <span class="tiny faint">${i.suggestions ? `${i.accepted}/${i.suggestions} accepted` : 'no suggestions'}</span></div>${meter(v)}`}</div></div>`;
}
function bindTailorList() {
  $$('[data-tsel]').forEach(c => c.onclick = e => { if (e.target.closest('[data-tdel]')) return; tailorSel = c.dataset.tsel; tailorTab = 'match'; renderTailor(); });
  $$('[data-tdel]').forEach(b => b.onclick = async () => { if (!confirm('Remove this job from tailoring? Its suggestions are deleted.')) return; await del(`/tailor/${enc(b.dataset.tdel)}`); renderTailor(); });
}
async function refreshTailorList() {
  const { items } = await api('/tailor');
  if ($('#tlist')) { $('#tlist').innerHTML = items.map(tailorCard).join(''); bindTailorList(); }
}

// ---------- resume map: your resume as it is, annotated for one job ----------
const hlText = (t, hl) => { let o = '', i = 0; for (const [s, e] of hl || []) { o += esc(t.slice(i, s)) + `<mark>${esc(t.slice(s, e))}</mark>`; i = e; } return o + esc(t.slice(i)); };
const addChip = x => `<span class="add-chip" title="${esc(x.why ? `The job: “${x.why}”` : '')}${x.other_resume ? ' · it is on your other resume' : ''}">+ ${esc(x.term)}${x.other_resume ? ' ↺' : ''}</span>`;
function resumeMapHtml(m) {
  const sec = (title, body) => `<div class="rm-sec"><div class="rm-h">${title}</div>${body}</div>`;
  const adds = (xs, note = '') => xs.length ? `<div class="rm-adds">${xs.map(addChip).join('')}${note ? ` <span class="tiny mut">${note}</span>` : ''}</div>` : '';
  let h = sec('Summary', `<p>${m.summary.text ? hlText(m.summary.text, m.summary.hl) : '<span class="mut">No summary — a one-line summary is a good place for the job’s title words.</span>'}</p>${adds(m.summary.adds)}`);
  if (m.skills.length) h += sec('Skills', m.skills.map(l => `<div class="rm-line">${hlText(l.text, l.hl)}${adds(l.adds)}</div>`).join(''));
  for (const [k, label] of [['experience', 'Experience'], ['projects', 'Projects'], ['other', 'Other']]) {
    const es = m.entries.filter(e => e.section === k);
    if (es.length) h += sec(label, es.map(e => `<div class="rm-entry"><div class="rm-eh">${esc(e.header)}</div><ul>${e.bullets.map(b => `<li>
      <span class="rm-id">${esc(b.id)}</span>${hlText(b.text, b.hl)}${b.supports.length ? ` <span class="rm-sup" title="${esc(b.supports.map(s => `• ${s.text}`).join('\n'))}">backs ${b.supports.length} requirement${b.supports.length > 1 ? 's' : ''}</span>` : ''}
      ${adds(b.adds, 'fits this bullet — work it in only if you used it here')}</li>`).join('')}</ul></div>`).join(''));
  }
  if (!m.skills.length && !m.entries.length) h += `<p class="small warn" style="margin-top:10px">Couldn’t find SKILLS / EXPERIENCE / PROJECTS headings with bullets in this resume, so there’s nothing to place keywords on. Use headings in capitals and ● bullets.</p>`;
  if (m.education.text) h += sec('Education', `<p>${hlText(m.education.text, m.education.hl)}</p>`);
  const gaps = m.gaps.length || m.unshown_requirements.length ? `<div class="rm-gaps"><b class="small">Nothing in your resume backs these</b>
    ${m.gaps.length ? `<div class="tags" style="margin-top:4px">${m.gaps.map(g => `<span class="tag warn" title="${esc(g.why)}">${esc(g.term)}</span>`).join('')}</div>` : ''}
    ${m.unshown_requirements.map(u => `<div class="tiny" style="margin-top:3px">• ${esc(u)}</div>`).join('')}
    <div class="tiny mut" style="margin-top:6px">Add one only if it’s true (e.g. a project bullet). Otherwise it’s an honest gap — leave it.</div></div>` : '';
  return `<div class="rm-legend tiny mut"><mark>green</mark> = keyword the job uses · <span class="add-chip">+ missing</span> = where it would fit (hover for the job’s wording) · <span class="rm-sup">backs N</span> = proves a requirement</div>
    <div class="rmap">${h}</div>${gaps}`;
}

function renderTailorDetail(r) {
  const a = r.now, acc = r.with_accepted, all = r.with_all, d = r.data || {}, sug = d.suggestions || [];
  const missing = a.keywords.filter(k => !k.in_resume), present = a.keywords.filter(k => k.in_resume);
  const weak = a.requirements.filter(q => q.status !== 'met');
  const pending = sug.filter(x => x.status === 'pending').length;
  const kwTag = k => `<span class="tag ${k.in_resume ? 'ok' : k.on_other_resume ? 'ac' : 'bad'}" title="${k.count}× in ${k.where.join(', ')}">${esc(k.term)}${k.weight >= 3 ? ' ★' : ''}${k.on_other_resume ? ' · other resume' : ''}</span>`;
  const reqRow = q => `<tr><td style="width:88px"><span class="tag ${STATUS_TAG[q.status][0]}">${STATUS_TAG[q.status][1]}</span></td><td>${esc(q.text)}
    ${q.kind === 'bullet' ? `<div class="tiny mut">closest bullet <b>${esc(q.evidence.id)}</b>: ${esc(q.evidence.text.slice(0, 120))}${q.evidence.text.length > 120 ? '…' : ''}</div>` : ''}
    ${q.missing_terms.length ? `<div class="tiny bad">needs: ${q.missing_terms.map(esc).join(', ')}</div>` : ''}</td></tr>`;
  const matchTab = `
    <div class="parts">${Object.entries(a.parts).map(([k, v]) => `<div><div class="row between small"><span>${PART_LABEL[k]}</span><b>${v}%${acc && acc.parts[k] !== v ? ` → <span class="ok">${acc.parts[k]}%</span>` : ''}</b></div>${meter(acc ? acc.parts[k] : v, 101)}</div>`).join('')}</div>
    <h3 style="margin:18px 0 6px">Missing keywords <span class="mut small">(${missing.length} of ${a.keywords.length})</span></h3>
    <div class="tags">${missing.slice(0, 12).map(kwTag).join('') || '<span class="small ok">Your resume has every keyword this job uses.</span>'}</div>
    ${missing.length > 12 || present.length ? `<details style="margin-top:6px"><summary class="small mut">All keywords</summary><div class="tags" style="margin-top:6px">${[...missing, ...present].map(kwTag).join('')}</div></details>` : ''}
    <p class="tiny faint" style="margin:6px 0 0">★ appears often or in required qualifications. Only add keywords that are true for you.</p>
    <h3 style="margin:18px 0 6px">Requirements to strengthen <span class="mut small">(${weak.length} of ${a.requirements.length})</span></h3>
    ${weak.length ? `<table class="t"><tbody>${weak.slice(0, 6).map(reqRow).join('')}</tbody></table>` : '<p class="small ok" style="margin:0">Every requirement is backed by a bullet.</p>'}
    <details style="margin-top:6px"><summary class="small mut">All ${a.requirements.length} requirements</summary><table class="t" style="margin-top:6px"><tbody>${a.requirements.map(reqRow).join('')}</tbody></table></details>
    <p class="small" style="margin:14px 0 0">Title words ${a.title.hits.map(t => `<span class="tag ok">${esc(t)}</span>`).join(' ')}${a.title.missing.map(t => `<span class="tag bad">${esc(t)}</span>`).join(' ')}
      · Soft skills ${a.soft_skills.map(x => `<span class="tag ${x.in_resume ? 'ok' : 'bad'}">${esc(x.skill)}</span>`).join(' ') || '<span class="mut">none asked</span>'}</p>
    <div class="row" style="margin-top:16px"><button class="btn" data-tab="map">See where they fit in your resume</button><button class="btn primary" data-tab="suggest">Next: get bullet suggestions →</button></div>`;
  const suggestTab = `
    <div class="sug" style="background:var(--panel-2)">
      <div class="row between"><b class="small">1 · Ask Claude</b><button class="btn sm primary" id="tprompt">Copy prompt</button></div>
      <p class="tiny mut" style="margin:4px 0 8px">Paste it into Claude (your plan), then paste Claude’s JSON reply here. Or run <code>/tailor-resume</code> in Claude Code.</p>
      <textarea id="treply" rows="3" style="width:100%" placeholder='{"suggestions": [...]}'></textarea>
      <div class="row" style="margin-top:6px"><button class="btn" id="timport">Import &amp; re-score</button>${sug.length ? `<span class="tiny mut">replaces the current ${sug.length}</span>` : ''}</div></div>
    ${sug.length || d.summary || (d.skills_to_add || []).length ? `
      <div class="row between" style="margin:16px 0 8px"><b class="small">2 · Review (${pending} left)</b>${all && acc && all.score !== acc.score ? `<span class="tiny mut">accepting the rest → ${Math.round(all.score)}%</span>` : ''}</div>
      <div class="stack">${sug.map(x => `<div class="sug ${x.status}">
        <div class="row between small"><b>${x.type === 'rewrite' ? `Rewrite ${esc(x.target)}` : `New bullet${x.target ? ` · ${esc(x.target)}` : ''}`}</b><span class="mut">${esc(x.why)}</span></div>
        ${x.original ? `<div class="del small">${esc(x.original)}</div>` : ''}
        <div class="ins small" contenteditable="true" data-edit="${esc(x.id)}" title="Click to edit">${esc(x.text)}</div>
        <div class="row" style="margin-top:6px">${x.adds.length ? `<span class="tiny">adds ${x.adds.map(t => `<span class="tag ok">${esc(t)}</span>`).join(' ')}</span>` : ''}
          ${x.removes.length ? `<span class="tiny bad">drops ${x.removes.map(esc).join(', ')}</span>` : ''}<span class="grow"></span>
          ${x.status === 'pending' ? `<button class="btn sm primary" data-dec="accepted" data-sid="${esc(x.id)}">Accept</button><button class="btn sm" data-dec="rejected" data-sid="${esc(x.id)}">Reject</button>`
            : `<span class="tag ${x.status === 'accepted' ? 'ok' : 'bad'}">${x.status}</span><button class="btn sm ghost" data-dec="pending" data-sid="${esc(x.id)}">Undo</button>`}</div></div>`).join('')}
        ${(d.skills_to_add || []).length ? `<div class="sug ${d.skills_status}"><div class="row small"><b>Add to Skills:</b> ${d.skills_to_add.map(esc).join(', ')}<span class="grow"></span>
          ${d.skills_status === 'pending' ? `<button class="btn sm primary" data-dec="accepted" data-sid="skills">Accept</button><button class="btn sm" data-dec="rejected" data-sid="skills">Reject</button>` : `<span class="tag">${d.skills_status}</span><button class="btn sm ghost" data-dec="pending" data-sid="skills">Undo</button>`}</div></div>` : ''}
        ${d.summary ? `<div class="sug ${d.summary_status}"><div class="row small"><b>Summary</b><span class="grow"></span>
          ${d.summary_status === 'pending' ? `<button class="btn sm primary" data-dec="accepted" data-sid="summary">Accept</button><button class="btn sm" data-dec="rejected" data-sid="summary">Reject</button>` : `<span class="tag">${d.summary_status}</span><button class="btn sm ghost" data-dec="pending" data-sid="summary">Undo</button>`}</div><div class="small">${esc(d.summary)}</div></div>` : ''}
        ${(d.gaps || []).length ? `<p class="small" style="margin:0"><b>Can’t honestly claim:</b> ${d.gaps.map(g => `<span class="tag warn">${esc(g)}</span>`).join(' ')}</p>` : ''}</div>
      <div class="row between sug" style="margin-top:16px;background:var(--panel-2)"><span class="small"><b>3 · Copy</b> <span class="mut">accepted changes as plain text, then edit your resume yourself</span></span>
        <button class="btn sm primary" id="texport">Copy accepted changes</button></div>` : ''}`;
  $('#tdetail').innerHTML = `<div class="card"><div class="bd">
    <div class="row between"><div><div class="small mut">${esc(r.job.company)}${r.job.url ? ` · <a href="${esc(r.job.url)}" target="_blank" rel="noopener">posting ↗</a>` : ''}</div><div class="title">${esc(r.job.title)}</div></div>
      <select id="tlane" style="width:auto" title="Resume to tailor">${r.lanes.map(l => `<option value="${esc(l)}" ${l === r.lane ? 'selected' : ''}>${esc(r.lane_labels[l] || l)} resume</option>`).join('')}</select></div>
    <div class="scores"><div><div class="big">${Math.round(a.score)}%</div><div class="small mut">match now</div></div>
      ${acc ? `<div><div class="big ok">${Math.round(acc.score)}%</div><div class="small mut">with accepted bullets</div></div>` : ''}
      <div><div class="big faint">90%</div><div class="small mut">target</div></div></div>
    ${meter(acc ? acc.score : a.score)}
    <div class="tabs"><button data-tab="match" class="${tailorTab === 'match' ? 'on' : ''}">Match</button>
      <button data-tab="map" class="${tailorTab === 'map' ? 'on' : ''}">Resume map</button>
      <button data-tab="suggest" class="${tailorTab === 'suggest' ? 'on' : ''}">Suggestions${pending ? ` (${pending})` : ''}</button></div>
    <div style="margin-top:14px">${tailorTab === 'match' ? matchTab : tailorTab === 'map' ? resumeMapHtml(r.map) : suggestTab}</div></div></div>`;
  $$('#tdetail [data-tab]').forEach(b => b.onclick = () => { tailorTab = b.dataset.tab; renderTailorDetail(r); });
  $('#tlane').onchange = async e => renderTailorDetail(await put(`/tailor/${enc(r.id)}`, { lane: e.target.value }));
  if ($('#tprompt')) $('#tprompt').onclick = async () => { const p = await api(`/tailor/${enc(r.id)}/prompt`); copyText(p.prompt, 'Prompt copied — paste it into Claude'); };
  if ($('#timport')) $('#timport').onclick = async () => {
    try { renderTailorDetail(await post(`/tailor/${enc(r.id)}/suggestions`, { text: $('#treply').value })); toast('Imported — review each suggestion'); refreshTailorList(); }
    catch (e) { toast(e.message); } };
  $$('#tdetail [data-dec]').forEach(b => b.onclick = async () => {
    const box = $(`[data-edit="${CSS.escape(b.dataset.sid)}"]`);
    renderTailorDetail(await put(`/tailor/${enc(r.id)}/suggestions/${enc(b.dataset.sid)}`, { status: b.dataset.dec, text: box ? box.innerText.trim() : null }));
    refreshTailorList();
  });
  if ($('#texport')) $('#texport').onclick = async () => copyText(await api(`/tailor/${enc(r.id)}/export`), 'Accepted changes copied as text');
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
      <div class="card"><div class="hd"><h2>Sources</h2></div><div class="bd small">${sourceList(st)}</div></div>
      <div class="card"><div class="hd"><h2>What the ranking learned</h2></div><div class="bd small" id="learned">…</div></div>
      <div class="card"><div class="hd"><h2>Data on this Mac</h2></div><div class="bd small"><div class="kv">
        <span>Jobs database</span><span><code>jobs.db</code> (postings, embeddings, tracker, emails)</span>
        <span>Personal files</span><span><code>data/</code> — resumes, settings, backups, model</span>
        <span>Leaves the Mac</span><span>Only requests for public job listings, the pages you open, and Gmail reads if connected.</span></div></div></div>
    </div>`;
  bindFields($('#page-settings'), async () => { try { const mode = S.schedule; S = await put('/settings', S); toast('Saved'); if (mode !== st.schedule?.mode) renderSettings(); } catch (e) { toast(e.message); } });
  $('#page-settings .card .hd h2').insertAdjacentHTML('afterend', '<button class="btn sm" id="runMorning">Run morning routine now</button>');
  $('#runMorning').onclick = async () => { const r = await post('/refresh?source=morning'); toast(r.started ? 'Morning routine started — takes a few minutes' : 'Already running'); };
  api('/personal').then(p => { $('#learned').innerHTML = p.active
    ? `Trained on ${p.positive} jobs you saved/applied to and ${p.negative} you marked not interested.<div class="tags" style="margin-top:6px">${(p.likes || []).map(x => `<span class="tag ok">likes ${esc(x)}</span>`).join('')}${(p.dislikes || []).map(x => `<span class="tag bad">avoids ${esc(x)}</span>`).join('')}</div>`
    : `Learns from your Save / Applied / Not interested clicks once it has ${p.needed ?? 5} of each (now ${p.positive ?? 0} and ${p.negative ?? 0}).`; }).catch(() => {});
  if ($('#backupNow')) $('#backupNow').onclick = async () => { const r = await post('/maintenance/backup'); toast(`Backed up to ${r.path.split('/').slice(-2).join('/')}`); };
}

// ---------- boot ----------
(async () => {
  await loadSettings().catch(e => toast(e.message));
  go(location.hash.slice(1) || 'home');
  loadStatus(); loadInboxCount();
  setInterval(() => { loadStatus(); loadInboxCount(); }, 60 * 1000);
})();
