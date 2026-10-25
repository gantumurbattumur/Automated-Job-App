// Find fillable controls and put values into them in a way React/Angular/Workday forms accept.
var JP = window.JP || (window.JP = {});

JP.sleep = ms => new Promise(r => setTimeout(r, ms));
JP.waitFor = async (fn, ms = 2500, step = 100) => {
  for (let t = 0; t < ms; t += step) { const v = fn(); if (v && (!Array.isArray(v) || v.length)) return v; await JP.sleep(step); }
  return fn();
};
const text = e => (e?.innerText || e?.textContent || '').replace(/\s+/g, ' ').trim();
JP.visible = e => { const r = e.getBoundingClientRect(), s = getComputedStyle(e); return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };

JP.labelOf = e => {
  if (e.id) { const l = document.querySelector(`label[for="${CSS.escape(e.id)}"]`); if (l && text(l)) return text(l); }
  const by = e.getAttribute('aria-labelledby');
  if (by) { const t = by.split(/\s+/).map(i => text(document.getElementById(i))).filter(Boolean).join(' '); if (t) return t; }
  if (e.getAttribute('aria-label') && e.tagName === 'BUTTON') return e.getAttribute('aria-label');
  const own = e.closest('label'); if (own && text(own)) return text(own);
  const fs = e.closest('fieldset'); if (fs?.querySelector('legend')) return text(fs.querySelector('legend'));
  const wd = e.closest('[data-automation-id^="formField"]'); if (wd?.querySelector('label')) return text(wd.querySelector('label'));
  let c = e.parentElement;
  for (let i = 0; i < 4 && c; i++, c = c.parentElement) {
    const l = c.querySelector('label, legend, [class*=label], [class*=question]');
    if (l && !l.contains(e) && text(l)) return text(l);
  }
  return e.getAttribute('aria-label') || e.placeholder || e.name || '';
};

// every control the user would fill, with its label and kind
JP.scan = (root = document) => {
  const out = [], seenGroups = new Set();
  for (const e of root.querySelectorAll('input, select, textarea, button[aria-haspopup="listbox"]')) {
    const t = (e.type || '').toLowerCase();
    if (e.closest('#jobprep-root')) continue;
    if (['hidden', 'submit', 'button', 'image', 'reset', 'search', 'password'].includes(t) && e.tagName !== 'BUTTON') continue;
    if (e.disabled || e.readOnly || e.name === 'g-recaptcha-response') continue;
    if (t !== 'file' && !JP.visible(e)) continue;
    if (t !== 'file' && (e.tabIndex === -1 || e.getAttribute('aria-hidden') === 'true')) continue;  // React's hidden twins
    if (e.closest('.iti__country-container, .iti__dropdown-content, [class*=country-select]')) continue;  // phone flag picker
    const label = JP.labelOf(e);
    const required = e.required || e.getAttribute('aria-required') === 'true' || /[*✱]\s*$/.test(label);
    let kind = e.tagName === 'SELECT' ? 'select' : e.tagName === 'TEXTAREA' ? 'textarea' : e.tagName === 'BUTTON' ? 'listbutton'
      : t === 'file' ? 'file' : t === 'radio' || t === 'checkbox' ? t
      : (e.getAttribute('role') === 'combobox' || e.getAttribute('aria-autocomplete')) ? 'combo' : 'text';
    let group = '';
    if (kind === 'radio' || kind === 'checkbox') {
      const g = e.closest('fieldset, [role=radiogroup], [role=group], [data-automation-id^="formField"]');
      group = g ? (g.querySelector('legend') ? text(g.querySelector('legend')) : JP.labelOf(g.querySelector('input') || e)) : label;
      if (kind === 'radio') { if (seenGroups.has(group)) continue; seenGroups.add(group); }  // one entry per question
    }
    out.push({ el: e, kind, label, required, group, options: kind === 'radio' ? [e] : null });
  }
  // radios: collect all options of each group
  for (const f of out) if (f.kind === 'radio') {
    const name = f.el.name;
    f.options = name ? [...root.querySelectorAll(`input[type=radio][name="${CSS.escape(name)}"]`)] : f.options;
  }
  return out;
};

const fire = (el, type, init = {}) => el.dispatchEvent(new Event(type, { bubbles: true, ...init }));
JP.setValue = (el, v) => {  // React tracks the native value setter; plain el.value= is ignored by controlled inputs
  const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : el.tagName === 'SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, v);
  fire(el, 'input'); fire(el, 'change');
};
const click = el => { for (const t of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) el.dispatchEvent(new MouseEvent(t, { bubbles: true, cancelable: true, view: window })); };
JP.click = click;

JP.fillText = (f, v) => { f.el.focus(); JP.setValue(f.el, v); f.el.blur(); fire(f.el, 'blur'); return true; };

JP.fillSelect = (f, v, ctx) => {
  const opts = [...f.el.options].filter(o => o.value !== '' || o.text.trim());
  const pick = JP.pick(opts.map(o => o.text.trim()), v, ctx);
  const o = opts.find(o => o.text.trim() === pick);
  if (!o) return false;
  JP.setValue(f.el, o.value);
  return true;
};

const optionsFor = el => {
  const ctl = el.getAttribute('aria-controls') || el.getAttribute('aria-owns');
  const scope = ctl && document.getElementById(ctl);
  return [...(scope || document).querySelectorAll('[role=option]')].filter(JP.visible);
};

JP.fillCombo = async (f, v, ctx) => {
  const el = f.el, decline = new RegExp(ctx.decline, 'i').test(v);
  el.focus(); click(el);
  if (!decline) JP.setValue(el, JP.query(f.label, v));  // "won't say" is worded differently everywhere: browse the full list
  else el.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }));
  const opts = await JP.waitFor(() => optionsFor(el), 2500);
  const pick = JP.pick(opts.map(text), v, ctx);
  const o = opts.find(o => text(o) === pick);
  if (!o) { el.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); if (!decline) JP.setValue(el, ''); return false; }
  o.scrollIntoView({ block: 'nearest' }); click(o);
  await JP.sleep(150);
  return true;
};

JP.fillListButton = async (f, v, ctx) => {  // Workday-style <button aria-haspopup="listbox">
  click(f.el);
  const opts = await JP.waitFor(() => [...document.querySelectorAll('[role=listbox] [role=option], [role=option]')].filter(JP.visible), 2500);
  const pick = JP.pick(opts.map(text), v, ctx);
  const o = opts.find(o => text(o) === pick);
  if (!o) { document.body.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); return false; }
  click(o);
  await JP.sleep(200);
  return true;
};

JP.fillRadio = (f, v, ctx) => {
  const labelOf = o => text(o.closest('label')) || text(document.querySelector(`label[for="${CSS.escape(o.id || '_')}"]`)) || o.value;
  const opts = f.options || [f.el];
  const pick = JP.pick(opts.map(labelOf), v, ctx);
  const o = opts.find(o => labelOf(o) === pick);
  if (!o) return false;
  if (!o.checked) click(o.closest('label') || o);
  if (!o.checked) { o.checked = true; fire(o, 'change'); }
  return true;
};

JP.fillCheckbox = (f, v, ctx) => {
  const own = text(f.el.closest('label')) || text(document.querySelector(`label[for="${CSS.escape(f.el.id || '_')}"]`));
  if (!JP.pick([own], v, ctx)) return false;
  if (!f.el.checked) click(f.el);
  return true;
};

JP.fillFile = async (f, ctx) => {
  const r = await chrome.runtime.sendMessage({ type: 'file', path: ctx.resume.url });
  if (!r?.ok) return false;
  const bytes = Uint8Array.from(atob(r.data.b64), c => c.charCodeAt(0));
  const file = new File([bytes], r.data.name || ctx.resume.filename, { type: r.data.type });
  const dt = new DataTransfer(); dt.items.add(file);
  f.el.files = dt.files;
  fire(f.el, 'input'); fire(f.el, 'change');
  const zone = f.el.closest('[class*=drop], [data-automation-id*=drop]');  // some widgets only listen for drops
  if (zone && !f.el.files.length) zone.dispatchEvent(new DragEvent('drop', { bubbles: true, dataTransfer: dt }));
  return true;
};

JP.flag = (el, on = true) => { el.style.outline = on ? '2px solid #f04438' : ''; el.style.outlineOffset = on ? '2px' : ''; };
